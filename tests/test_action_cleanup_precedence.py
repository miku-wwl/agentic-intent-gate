"""Action error identity, real disk lock safety and actual loopback HTTP regressions."""

import asyncio
import socket
import sqlite3
import threading
import time
from contextlib import closing, contextmanager
from dataclasses import replace
from functools import partial
from uuid import uuid4

import httpx
import pytest
import uvicorn
from action_helpers import cmd
from execution_process_helpers import record_fact
from run_helpers import database_rows
from test_service_demo_adapter import downstream

import agentic_intent_gate.infrastructure.persistence.sqlite_actions as sqlite_actions
from agentic_intent_gate.api.local_http import create_app
from agentic_intent_gate.application.action_execution import ActionExecutionGateway
from agentic_intent_gate.application.generate_plan import PlanningService
from agentic_intent_gate.application.recover_actions import recover_actions
from agentic_intent_gate.application.runs import RunService
from agentic_intent_gate.domain.action import ActionCode, ActionError, ActionState, safe_result
from agentic_intent_gate.domain.approval_binding import BindingCode, BindingError
from agentic_intent_gate.domain.run import RunErrorCode, RunStoreError
from agentic_intent_gate.infrastructure.persistence import sqlite_action_execution
from agentic_intent_gate.infrastructure.security.single_owner import single_owner
from agentic_intent_gate.ports.action_repository import AdmissionCommand

MODES = ("normal", "rollback", "inspection", "close", "rollback-close")


class RetainedFault:
    """Real connections remain strongly held through independent writer verification."""

    def __init__(self, mode="normal", *, write=False, commit="", close_control=None):
        self.mode, self.write, self.commit_mode = mode, write, commit
        self.close_control = close_control
        self.connections = []
        self.acquisitions = 0

    @contextmanager
    def installed(self, monkeypatch):
        fault = self

        class Connection(sqlite3.Connection):
            rollback_attempts = 0
            close_attempts = 0
            commit_attempts = 0
            transaction_before_close = None
            armed = False

            def execute(self, statement, parameters=()):
                cursor = super().execute(statement, parameters)
                if statement.startswith("BEGIN"):
                    self.armed = True
                if fault.write and statement.startswith("INSERT INTO action_authorizations"):
                    raise sqlite3.OperationalError("controlled private write detail")
                return cursor

            @property
            def in_transaction(self):
                if self.armed and fault.mode == "inspection":
                    raise sqlite3.OperationalError("controlled private inspection detail")
                return super().in_transaction

            def rollback(self):
                self.rollback_attempts += 1
                if fault.mode in {"rollback", "rollback-close", "refuse"}:
                    raise sqlite3.OperationalError("controlled private rollback detail")
                super().rollback()

            def commit(self):
                self.commit_attempts += 1
                if fault.commit_mode == "before":
                    raise sqlite3.OperationalError("controlled private pre-commit detail")
                super().commit()
                if fault.commit_mode == "after":
                    raise sqlite3.OperationalError("controlled private lost commit acknowledgement")

            def close(self):
                self.close_attempts += 1
                self.transaction_before_close = sqlite3.Connection.in_transaction.__get__(self)
                if fault.mode != "refuse":
                    super().close()
                if fault.close_control is not None:
                    raise fault.close_control
                if fault.mode in {"close", "rollback-close", "refuse"}:
                    raise sqlite3.OperationalError("controlled private close acknowledgement")

        def open_fault(path):
            self.acquisitions += 1
            if self.mode == "acquire":
                raise sqlite3.OperationalError("controlled private acquisition detail")
            # HTTP owns the transaction on its server thread; the controller only accesses
            # the retained handle after the request completes, never concurrently.
            connection = sqlite3.connect(
                path, isolation_level=None, factory=Connection, check_same_thread=False
            )
            self.connections.append(connection)
            return connection

        try:
            with monkeypatch.context() as patch:
                patch.setattr(sqlite_actions, "open_existing", open_fault)
                yield self
        finally:
            # After assertions, explicitly release even intentionally nonfunctional wrappers.
            for connection in self.connections:
                try:
                    sqlite3.Connection.rollback(connection)
                except sqlite3.ProgrammingError:
                    pass
                finally:
                    sqlite3.Connection.close(connection)

    def counts(self):
        return {
            "rollback_attempts": sum(c.rollback_attempts for c in self.connections),
            "close_attempts": sum(c.close_attempts for c in self.connections),
            "commit_attempts": sum(c.commit_attempts for c in self.connections),
            "acquisitions": self.acquisitions,
        }


def snapshot(e):
    return e.ledger(), database_rows(e.runs.path), downstream(e.demo_path)


def state(e):
    with closing(sqlite3.connect(e.runs.path, timeout=0.05)) as connection:
        row = connection.execute("SELECT state,version FROM actions").fetchone()
        return {
            "action_state": row[0],
            "action_version": row[1],
            "grants": connection.execute("SELECT COUNT(*) FROM action_authorizations").fetchone()[
                0
            ],
            "attempts": connection.execute("SELECT COUNT(*) FROM action_attempts").fetchone()[0],
            "notes": len(downstream(e.demo_path)["notes"]),
            "receipts": len(downstream(e.demo_path)["receipts"]),
            "adapter_calls": e.calls,
        }


def writer_commit(e):
    started = time.monotonic()
    try:
        with closing(sqlite3.connect(e.runs.path, isolation_level=None, timeout=0.05)) as writer:
            writer.execute("BEGIN IMMEDIATE")
            writer.execute("UPDATE runs SET intent=intent WHERE run_id=?", (e.run.run_id,))
            assert writer.execute("SELECT changes()").fetchone() == (1,)
            writer.commit()
        return {"result": "PASS", "seconds": time.monotonic() - started}
    except sqlite3.OperationalError:
        return {"result": "BLOCKED", "seconds": time.monotonic() - started}


def observed(e, fault, *, scenario, code, expected, writer, **extra):
    record_fact(
        "action-precedence",
        scenario=scenario,
        cleanup=fault.mode,
        expected_code=expected,
        observed_code=code,
        strong_reference_held=bool(fault.connections),
        writer=writer,
        **fault.counts(),
        **state(e),
        **extra,
    )


@pytest.mark.parametrize("repetition", [1, 2])
@pytest.mark.parametrize("mode", MODES)
def test_stale_authorization_keeps_primary_and_releases_lock(
    demo_env, monkeypatch, mode, repetition
):
    e, primary = demo_env, []
    before, original_guard = snapshot(e), sqlite_action_execution.guard_action

    def capture_guard(*args):
        try:
            return original_guard(*args)
        except ActionError as error:
            primary.append(error)
            raise

    fault = RetainedFault(mode)
    with fault.installed(monkeypatch), monkeypatch.context() as patch:
        patch.setattr(sqlite_action_execution, "guard_action", capture_guard)
        with pytest.raises(ActionError) as error:
            e.repo.authorize(cmd(e.action, version=2), e.principal, e.policy)
        writer = writer_commit(e)
        observed(
            e,
            fault,
            scenario="stale-authorization",
            code=error.value.code.value,
            expected=ActionCode.STALE_VERSION.value,
            writer=writer,
            repetition=repetition,
            primary_identity_preserved=error.value is primary[0],
        )
        assert snapshot(e) == before and writer["result"] == "PASS"
        assert fault.counts()["close_attempts"] == 1
        assert fault.counts()["rollback_attempts"] == int(mode != "inspection")
        assert error.value is primary[0] and error.value.code is ActionCode.STALE_VERSION
        if mode != "normal":
            assert error.value.__notes__ == ["Action transaction cleanup failed."]


@pytest.mark.parametrize("mode", ["rollback", "inspection", "close"])
@pytest.mark.parametrize(
    "scenario",
    [
        "idempotency",
        "authorization",
        "claim-stale",
        "claim-started",
        "result-attempt",
        "run-stale",
        "run-missing",
        "binding-missing",
    ],
)
def test_real_business_errors_cross_shared_boundary(demo_env, monkeypatch, scenario, mode):
    e = demo_env
    command = cmd(e.action)
    expected_type, expected_code = ActionError, ActionCode.IDEMPOTENCY_CONFLICT
    if scenario in {
        "idempotency",
        "authorization",
        "claim-stale",
        "claim-started",
        "result-attempt",
    }:
        authorized = e.repo.authorize(command, e.principal, e.policy).action
    if scenario == "idempotency":
        operation = partial(
            e.repo.authorize, replace(command, expected_action_version=2), e.principal, e.policy
        )
    elif scenario == "authorization":
        e.policy.policy_revision = "changed-policy-v2"
        expected_code = ActionCode.NOT_AUTHORIZED
        operation = partial(e.repo.authorize, command, e.principal, e.policy)
    elif scenario == "claim-stale":
        expected_code = ActionCode.STALE_VERSION
        operation = partial(e.repo.claim, cmd(authorized, version=1), e.principal, e.policy)
    elif scenario in {"claim-started", "result-attempt"}:
        claimed = e.repo.claim(cmd(authorized), e.principal, e.policy).action
        if scenario == "claim-started":
            expected_code = ActionCode.ALREADY_STARTED
            operation = partial(e.repo.claim, cmd(claimed), e.principal, e.policy)
        else:
            expected_code = ActionCode.NOT_EXECUTABLE
            operation = partial(
                e.repo.finish, claimed.action_id, str(uuid4()), safe_result(ActionState.Unknown)
            )
    else:
        run_id, version = e.run.run_id, 5
        if scenario == "run-stale":
            version = 4
            expected_type, expected_code = RunStoreError, RunErrorCode.STALE_RUN_VERSION
        elif scenario == "run-missing":
            run_id = str(uuid4())
            expected_type, expected_code = RunStoreError, RunErrorCode.RUN_NOT_FOUND
        else:
            with closing(sqlite3.connect(e.runs.path)) as connection:
                connection.execute("DELETE FROM approval_plan_bindings")
                connection.commit()
            expected_type, expected_code = BindingError, BindingCode.MISSING
        admission = AdmissionCommand(run_id, version, e.principal.subject_id, str(uuid4()))
        operation = partial(e.repo.admit, admission, principal=e.principal, policy=e.policy)
    before, fault = snapshot(e), RetainedFault(mode)
    with fault.installed(monkeypatch):
        with pytest.raises(expected_type) as error:
            operation()
        writer = writer_commit(e)
        observed(
            e,
            fault,
            scenario=scenario,
            code=error.value.code.value,
            expected=expected_code.value,
            writer=writer,
            exception_type=type(error.value).__name__,
        )
        assert error.value.code is expected_code
        assert snapshot(e) == before and writer["result"] == "PASS"
        assert fault.counts()["close_attempts"] == 1
        assert e.calls == 0 and downstream(e.demo_path) == {
            "resource": [(1, e.action.spec.execution_scope)],
            "notes": [],
            "receipts": [],
        }


def test_acknowledged_success_preserves_authorization_replay(demo_env, monkeypatch):
    e, command, fault = demo_env, cmd(demo_env.action), RetainedFault()
    with fault.installed(monkeypatch):
        result = e.repo.authorize(command, e.principal, e.policy)
        writer = writer_commit(e)
        observed(e, fault, scenario="success", code="SUCCESS", expected="SUCCESS", writer=writer)
        assert (result.action.state, result.action.version) == (ActionState.Authorized, 2)
        assert fault.counts()["commit_attempts"] == fault.counts()["close_attempts"] == 1
    stable = snapshot(e)
    assert e.repo.authorize(command, e.principal, e.policy).replayed
    assert snapshot(e) == stable and writer["result"] == "PASS"


@pytest.mark.parametrize("mode", ["normal", "rollback", "rollback-close"])
def test_primary_write_failure_translates_and_rolls_back(demo_env, monkeypatch, mode):
    e, fault = demo_env, RetainedFault(mode, write=True)
    before = snapshot(e)
    with fault.installed(monkeypatch):
        with pytest.raises(ActionError) as error:
            e.repo.authorize(cmd(e.action), e.principal, e.policy)
        writer = writer_commit(e)
        observed(
            e,
            fault,
            scenario="write-failure",
            code=error.value.code.value,
            expected=ActionCode.DATABASE_UNAVAILABLE.value,
            writer=writer,
        )
        assert error.value.code is ActionCode.DATABASE_UNAVAILABLE
        assert "controlled private" not in str(error.value)
        assert snapshot(e) == before and writer["result"] == "PASS"
        assert fault.counts()["rollback_attempts"] == fault.counts()["close_attempts"] == 1


@pytest.mark.parametrize("case", ["before", "after", "close"])
def test_authorization_commit_uncertainty_never_fabricates_success(demo_env, monkeypatch, case):
    e, command = demo_env, cmd(demo_env.action)
    fault = RetainedFault(
        "close" if case == "close" else "normal", commit="" if case == "close" else case
    )
    before = snapshot(e)
    with fault.installed(monkeypatch):
        with pytest.raises(ActionError) as error:
            e.repo.authorize(command, e.principal, e.policy)
        writer = writer_commit(e)
        observed(
            e,
            fault,
            scenario="authorization-commit-" + case,
            code=error.value.code.value,
            expected=ActionCode.DATABASE_UNAVAILABLE.value,
            writer=writer,
            committed=case != "before",
            automatic_retry=False,
        )
        assert error.value.code is ActionCode.DATABASE_UNAVAILABLE
        assert writer["result"] == "PASS" and fault.counts()["close_attempts"] == 1
        assert fault.counts()["rollback_attempts"] == int(case == "before")
        if case == "before":
            assert snapshot(e) == before
        else:
            assert state(e) == dict(
                action_state="Authorized",
                action_version=2,
                grants=1,
                attempts=0,
                notes=0,
                receipts=0,
                adapter_calls=0,
            )
    result = e.repo.authorize(command, e.principal, e.policy)
    assert result.replayed is (case != "before")
    assert len(e.ledger()["action_authorizations"]) == 1 and e.calls == 0


@pytest.mark.parametrize("case", ["before", "after", "close"])
async def test_unconfirmed_claim_never_dispatches_or_retries(execution_env, monkeypatch, case):
    e, command = execution_env, cmd(execution_env.action)
    fault = RetainedFault(
        "close" if case == "close" else "normal", commit="" if case == "close" else case
    )
    before = snapshot(e)
    with fault.installed(monkeypatch):
        with pytest.raises(ActionError) as error:
            await e.gateway.execute(command, e.principal)
        writer = writer_commit(e)
        observed(
            e,
            fault,
            scenario="claim-commit-" + case,
            code=error.value.code.value,
            expected=ActionCode.DATABASE_UNAVAILABLE.value,
            writer=writer,
            committed=case != "before",
            automatic_retry=False,
        )
        assert error.value.code is ActionCode.DATABASE_UNAVAILABLE
        assert writer["result"] == "PASS" and e.calls == 0
    if case == "before":
        assert snapshot(e) == before
        assert len(e.ledger()["action_attempts"]) == 0
    else:
        assert (e.repo.get(e.action.action_id).state, len(e.ledger()["action_attempts"])) == (
            ActionState.Executing,
            1,
        )
        assert (await e.gateway.execute(command, e.principal)).replayed
        assert recover_actions(e.repo, predecessor_stopped=True) == 1
        assert e.repo.get(e.action.action_id).state is ActionState.Unknown
        stable = snapshot(e)
        assert (await e.gateway.execute(command, e.principal)).replayed
        with pytest.raises(ActionError) as error:
            await e.gateway.execute(cmd(e.repo.get(e.action.action_id)), e.principal)
        assert error.value.code is ActionCode.ALREADY_STARTED
        assert snapshot(e) == stable and recover_actions(e.repo, predecessor_stopped=True) == 0
    assert e.calls == 0 and downstream(e.demo_path) == {
        "resource": [(1, e.action.spec.execution_scope)],
        "notes": [],
        "receipts": [],
    }


def test_acquisition_failure_has_no_owned_connection_or_mutation(demo_env, monkeypatch):
    e, fault = demo_env, RetainedFault("acquire")
    before = snapshot(e)
    with fault.installed(monkeypatch):
        with pytest.raises(ActionError) as error:
            e.repo.authorize(cmd(e.action), e.principal, e.policy)
        writer = writer_commit(e)
        observed(
            e,
            fault,
            scenario="acquisition",
            code=error.value.code.value,
            expected=ActionCode.DATABASE_UNAVAILABLE.value,
            writer=writer,
        )
        assert error.value.code is ActionCode.DATABASE_UNAVAILABLE
        assert not fault.connections and fault.acquisitions == 1
        assert snapshot(e) == before and writer["result"] == "PASS"


@pytest.mark.parametrize("error_type", [asyncio.CancelledError, KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("mode", ["normal", "rollback", "inspection", "close"])
def test_primary_control_flow_is_not_reclassified(demo_env, monkeypatch, error_type, mode):
    e, fault, primary = demo_env, RetainedFault(mode), error_type()
    before = snapshot(e)
    with fault.installed(monkeypatch):
        with pytest.raises(error_type) as error, e.repo._transaction(write=True) as connection:
            connection.execute("UPDATE runs SET intent=intent")
            raise primary
        writer = writer_commit(e)
        observed(
            e,
            fault,
            scenario="control-flow-" + error_type.__name__,
            code=type(error.value).__name__,
            expected=error_type.__name__,
            writer=writer,
            primary_identity_preserved=error.value is primary,
        )
        assert error.value is primary and writer["result"] == "PASS"
        assert snapshot(e) == before and fault.counts()["close_attempts"] == 1


@pytest.mark.parametrize("error_type", [asyncio.CancelledError, KeyboardInterrupt, SystemExit])
def test_new_cleanup_control_flow_is_propagated(demo_env, monkeypatch, error_type):
    e, control = demo_env, error_type()
    fault = RetainedFault(close_control=control)
    with fault.installed(monkeypatch):
        with pytest.raises(error_type) as error:
            e.repo.authorize(cmd(e.action), e.principal, e.policy)
        writer = writer_commit(e)
        observed(
            e,
            fault,
            scenario="cleanup-control-flow",
            code=type(error.value).__name__,
            expected=error_type.__name__,
            writer=writer,
            committed=True,
        )
        assert error.value is control and writer["result"] == "PASS"
        assert state(e)["grants"] == 1 and state(e)["attempts"] == 0


def test_refused_physical_close_is_not_reported_as_release(demo_env, monkeypatch):
    e, fault = demo_env, RetainedFault("refuse")
    before = snapshot(e)
    with fault.installed(monkeypatch):
        with pytest.raises(ActionError) as error:
            e.repo.authorize(cmd(e.action, version=2), e.principal, e.policy)
        blocked = writer_commit(e)
        observed(
            e,
            fault,
            scenario="physical-close-refusal",
            code=error.value.code.value,
            expected=ActionCode.STALE_VERSION.value,
            writer=blocked,
            physical_release_claimed=False,
        )
        assert error.value.code is ActionCode.STALE_VERSION
        assert fault.counts()["close_attempts"] == 1 and blocked["result"] == "BLOCKED"
        assert snapshot(e) == before
    assert writer_commit(e)["result"] == "PASS"


@contextmanager
def real_server(e):
    class ForbiddenProvider:
        calls = 0

        async def generate_plan(self, *args, **kwargs):
            self.calls += 1
            raise AssertionError("Action commands must not generate a Plan")

    provider = ForbiddenProvider()
    planning = PlanningService(provider, e.runs.validator)
    gateway = ActionExecutionGateway(e.repo, e.policy, e.registry)
    app = create_app(
        planning,
        run_service=RunService(e.runs, planning),
        action_gateway=gateway,
        capability_verifier=e.verifier,
    )
    with single_owner(e.runs.path, predecessor_stopped=True):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(app, log_level="critical", access_log=False, lifespan="off")
        )
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]})
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert server.started
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=10
            ) as client:
                yield client
        finally:
            server.should_exit = True
            thread.join(10)
            listener.close()
            with socket.socket() as probe:
                released = probe.connect_ex(("127.0.0.1", port)) != 0
            record_fact(
                "action-precedence-server",
                port=port,
                thread_stopped=not thread.is_alive(),
                listener_closed=released,
                provider_calls=provider.calls,
            )
            assert not thread.is_alive() and released and provider.calls == 0


@pytest.mark.parametrize("mode", [*MODES, "write", "acquire"])
def test_actual_http_preserves_conflict_and_storage_distinction(demo_env, monkeypatch, mode):
    e = demo_env
    fault = RetainedFault("normal" if mode == "write" else mode, write=mode == "write")
    before = snapshot(e)
    storage = mode in {"write", "acquire"}
    status = 503 if storage else 409
    code = ActionCode.DATABASE_UNAVAILABLE if storage else ActionCode.STALE_VERSION
    with real_server(e) as client:
        key = str(uuid4())
        headers = {"Authorization": "Bearer " + e.token, "Idempotency-Key": key}
        body = {"expected_action_version": 1 if storage else 2}
        route = f"/api/actions/{e.action.action_id}/authorize"
        with fault.installed(monkeypatch):
            response = client.post(route, json=body, headers=headers)
            writer = writer_commit(e)
            observed(
                e,
                fault,
                scenario="real-http-" + mode,
                code=response.json()["error"]["code"],
                expected=code.value,
                writer=writer,
                expected_status=status,
                actual_status=response.status_code,
                business_rows_unchanged=snapshot(e) == before,
            )
            assert response.status_code == status
            assert response.json()["error"] == {
                "code": code.value,
                "message": str(ActionError(code)),
            }
            assert all(
                text not in response.text
                for text in (
                    "controlled private",
                    "Traceback",
                    "OperationalError",
                    e.token,
                    str(e.runs.path),
                )
            )
            assert snapshot(e) == before and writer["result"] == "PASS"
            assert state(e) == dict(
                action_state="Pending",
                action_version=1,
                grants=0,
                attempts=0,
                notes=0,
                receipts=0,
                adapter_calls=0,
            )
        # Explicit subsequent healthy command, not an automatic retry of a rejected operation.
        healthy = client.post(
            route,
            json={"expected_action_version": 1},
            headers=dict(headers, **{"Idempotency-Key": str(uuid4())}),
        )
        assert healthy.status_code == 200 and healthy.json()["action"]["state"] == "Authorized"
        assert state(e) == dict(
            action_state="Authorized",
            action_version=2,
            grants=1,
            attempts=0,
            notes=0,
            receipts=0,
            adapter_calls=0,
        )
        assert e.token not in healthy.text
