"""Real v2 transactions/HTTP: retained faulty handles must not retain a write lock."""

import asyncio
import socket
import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import dataclass

import httpx
import pytest
import uvicorn
from execution_process_helpers import record_fact
from run_helpers import advance

import agentic_intent_gate.infrastructure.persistence.sqlite_v2_runs as sqlite_v2_runs
from agentic_intent_gate.api.local_http import create_app
from agentic_intent_gate.application.generate_plan import PlanningService
from agentic_intent_gate.application.runs import RunService
from agentic_intent_gate.domain.approval import ApprovalChoice, ApprovalDecisionRequest
from agentic_intent_gate.domain.approval_binding import BindingCode, BindingError
from agentic_intent_gate.domain.contracts import Risk
from agentic_intent_gate.domain.run import RUN_MESSAGES, RunErrorCode, RunState, RunStoreError
from agentic_intent_gate.infrastructure.persistence.sqlite_v2_schema import TABLES


@dataclass
class Fault:
    write_error: BaseException | None = None
    commit: str = "normal"
    inspect: bool = False
    rollback: bool = False
    close: bool = False
    acquire: bool = False
    arm_on_open: bool = False


@pytest.fixture
def v2(tmp_path, validator):
    return sqlite_v2_runs.SQLiteV2RunRepository(tmp_path / "v2.sqlite3", validator)


@pytest.fixture
def faults():
    opened = []  # Strong references survive the independent writer assertion; no GC rescue.

    def install(fault, patcher):
        class Connection(sqlite3.Connection):
            armed = fault.arm_on_open
            rollback_attempts = 0
            close_attempts = 0
            transaction_before_close = None

            def execute(self, sql, parameters=()):
                if sql.startswith("INSERT INTO audit_events"):
                    self.armed = True
                    if fault.write_error is not None:
                        raise fault.write_error
                return super().execute(sql, parameters)

            def commit(self):
                if self.armed and fault.commit == "before":
                    raise sqlite3.OperationalError("owned commit-before fault")
                super().commit()
                if self.armed and fault.commit in {"after", "after-close"}:
                    if fault.commit == "after-close":
                        sqlite3.Connection.close(self)
                    raise sqlite3.OperationalError("owned lost commit acknowledgement")

            @property
            def in_transaction(self):
                if self.armed and fault.inspect:
                    raise sqlite3.OperationalError("owned state inspection fault")
                return super().in_transaction

            def rollback(self):
                self.rollback_attempts += 1
                if self.armed and fault.rollback:
                    raise sqlite3.OperationalError("owned rollback fault")
                return super().rollback()

            def close(self):
                self.close_attempts += 1
                try:
                    self.transaction_before_close = sqlite3.Connection.in_transaction.__get__(self)
                except sqlite3.ProgrammingError:
                    self.transaction_before_close = "already closed"
                super().close()  # Real release, then simulate lost close acknowledgement.
                if self.armed and fault.close:
                    raise sqlite3.OperationalError("owned close acknowledgement fault")

        def connect(path):
            if fault.acquire:
                raise sqlite3.OperationalError("owned connection acquisition fault")
            connection = sqlite3.connect(
                path, isolation_level=None, factory=Connection, check_same_thread=False
            )
            opened.append(connection)
            return connection

        patcher.setattr(sqlite_v2_runs, "open_existing", connect)
        return opened

    yield install
    # Explicit owned teardown also releases handles when testing the unfixed source.
    for connection in opened:
        try:
            sqlite3.Connection.rollback(connection)
        except sqlite3.ProgrammingError:
            pass
        finally:
            sqlite3.Connection.close(connection)


def snapshot(path):
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        return {
            table: connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
            for table in TABLES
        }


def released(path, opened, *, rollback_attempts):
    assert len(opened) == 1
    connection = opened[0]
    assert connection.close_attempts == 1
    assert connection.rollback_attempts == rollback_attempts
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")
    started = time.monotonic()
    with closing(sqlite3.connect(path, isolation_level=None, timeout=0.05)) as writer:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE runs SET intent=intent")
        writer.commit()
    record_fact(
        "v2-run-lock",
        close_attempts=connection.close_attempts,
        rollback_attempts=connection.rollback_attempts,
        transaction_before_close=connection.transaction_before_close,
        strong_reference_retained=True,
        independent_writer="PASS",
        residual_lock=False,
        writer_seconds=time.monotonic() - started,
    )


def durable(repo, before, committed):
    rows = snapshot(repo.path)
    if not committed:
        assert rows == before
    else:
        assert len(rows["runs"]) == 1
        assert len(rows["approval_decisions"]) == 1
        assert len(rows["approval_plan_bindings"]) == 1
        assert len(rows["audit_events"]) == 5
        assert rows["approval_plan_bindings"][0][1] == "BOUND_V1"
        for table in TABLES.keys() - {
            "runs",
            "approval_decisions",
            "approval_plan_bindings",
            "audit_events",
        }:
            assert rows[table] == []
    reopened = sqlite_v2_runs.SQLiteV2RunRepository(repo.path, repo.validator)
    run_id = before["runs"][0][0]
    run = reopened.get(run_id)
    assert run.state == (RunState.Approved if committed else RunState.AwaitingApproval)
    assert run.version == (5 if committed else 4)
    if committed:
        for version in (4, 5):
            with pytest.raises(RunStoreError) as raised:
                reopened.decide_approval(
                    run_id, ApprovalDecisionRequest(ApprovalChoice.APPROVE, version)
                )
            assert raised.value.code == (
                RunErrorCode.STALE_RUN_VERSION
                if version == 4
                else RunErrorCode.INVALID_RUN_TRANSITION
            )
        assert snapshot(repo.path) == rows
    return run


CASES = [
    ("01-normal", Fault(), True, 0),
    ("02-write", Fault(write_error=sqlite3.OperationalError("owned write fault")), False, 1),
    ("03-primary", Fault(), False, 1),
    ("04-primary-rollback", Fault(rollback=True), False, 1),
    ("05-inspection", Fault(inspect=True), True, 0),
    ("06-primary-rollback-close", Fault(rollback=True, close=True), False, 1),
    ("07-close", Fault(close=True), True, 0),
    ("08-commit-before", Fault(commit="before"), False, 1),
    ("09-commit-after-close", Fault(commit="after-close"), True, 0),
    ("10-acquisition", Fault(acquire=True), False, 0),
]


@pytest.mark.parametrize("case,fault,committed,rollbacks", CASES, ids=[c[0] for c in CASES])
def test_approval_cleanup_matrix(v2, faults, monkeypatch, case, fault, committed, rollbacks):
    run = advance(v2, RunState.AwaitingApproval, Risk.SIDE_EFFECT)
    before = snapshot(v2.path)
    primary = None
    if "primary" in case:
        primary = RunStoreError(RunErrorCode.DATABASE_UNAVAILABLE, run.run_id)
        fault = Fault(write_error=primary, rollback=fault.rollback, close=fault.close)
    with monkeypatch.context() as patcher:
        opened = faults(fault, patcher)
        if case == "01-normal":
            assert (
                v2.decide_approval(
                    run.run_id, ApprovalDecisionRequest(ApprovalChoice.APPROVE, 4)
                ).state
                == RunState.Approved
            )
        else:
            with pytest.raises(RunStoreError) as raised:
                v2.decide_approval(run.run_id, ApprovalDecisionRequest(ApprovalChoice.APPROVE, 4))
            assert raised.value.code == RunErrorCode.DATABASE_UNAVAILABLE
            assert raised.value.run_id == run.run_id
            assert str(raised.value) == RUN_MESSAGES[RunErrorCode.DATABASE_UNAVAILABLE]
            if primary is not None:
                assert raised.value is primary
        if fault.acquire:
            assert opened == []
        else:
            released(v2.path, opened, rollback_attempts=rollbacks)
    after = durable(v2, before, committed)
    record_fact(
        "v2-run-matrix",
        case=case,
        result="PASS",
        state=after.state.value,
        version=after.version,
        decisions=int(committed),
        bindings=int(committed),
        audit_events=4 + int(committed),
        auto_retry=False,
    )


@pytest.mark.parametrize("kind", ["run", "binding", "cancelled"])
@pytest.mark.parametrize("cleanup", ["normal", "inspection", "rollback", "close"])
def test_primary_exception_identity(v2, faults, monkeypatch, kind, cleanup):
    primary = {
        "run": RunStoreError(RunErrorCode.STALE_RUN_VERSION, "owned-run"),
        "binding": BindingError(BindingCode.MISMATCH),
        "cancelled": asyncio.CancelledError(),
    }[kind]
    before = snapshot(v2.path)
    fault = Fault(
        inspect=cleanup == "inspection",
        rollback=cleanup == "rollback",
        close=cleanup == "close",
        arm_on_open=True,
    )
    with monkeypatch.context() as patcher:
        opened = faults(fault, patcher)
        with pytest.raises(type(primary)) as raised, v2._transaction(write=True):
            raise primary
        assert raised.value is primary
        released(v2.path, opened, rollback_attempts=int(cleanup != "inspection"))
    assert snapshot(v2.path) == before
    record_fact("v2-run-precedence", primary=kind, cleanup=cleanup, identity_preserved=True)


@pytest.fixture
def http_server(v2, provider_factory):
    provider = provider_factory()
    planning = PlanningService(provider, v2.validator)
    app = create_app(planning, run_service=RunService(v2, planning))
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=5
        ) as client:
            assert client.get("/").status_code == 200
            yield client
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive()
        with socket.socket() as probe:
            assert probe.connect_ex(("127.0.0.1", port)) != 0
        assert provider.calls == 0
        record_fact(
            "v2-run-http-cleanup",
            port=port,
            stopped=True,
            listener_closed=True,
            model_calls=provider.calls,
        )


HTTP_CASES = [
    ("normal", Fault(), True, 0),
    ("rollback", Fault(commit="before", rollback=True), False, 1),
    ("inspection", Fault(inspect=True), True, 0),
    ("close", Fault(close=True), True, 0),
    ("commit-before", Fault(commit="before"), False, 1),
    ("commit-after", Fault(commit="after"), True, 0),
    ("commit-after-close", Fault(commit="after-close"), True, 0),
]


@pytest.mark.parametrize(
    "case,fault,committed,rollbacks", HTTP_CASES, ids=[c[0] for c in HTTP_CASES]
)
def test_real_http_cleanup(v2, faults, monkeypatch, http_server, case, fault, committed, rollbacks):
    run = advance(v2, RunState.AwaitingApproval, Risk.SIDE_EFFECT)
    before = snapshot(v2.path)
    body = {"decision": "APPROVE", "expected_version": 4}
    with monkeypatch.context() as patcher:
        opened = faults(fault, patcher)
        response = http_server.post(f"/api/runs/{run.run_id}/approval", json=body)
        expected = 200 if case == "normal" else 503
        assert response.status_code == expected
        if expected == 503:
            assert response.json() == {
                "run_id": run.run_id,
                "error": {
                    "code": "DATABASE_UNAVAILABLE",
                    "message": RUN_MESSAGES[RunErrorCode.DATABASE_UNAVAILABLE],
                },
            }
        else:
            assert response.json()["state"] == "Approved"
        released(v2.path, opened, rollback_attempts=rollbacks)
    after = durable(v2, before, committed)
    # Healthy storage after the fault: explicit commands only, never inferred approval replay.
    if committed:
        stable = snapshot(v2.path)
        stale = http_server.post(f"/api/runs/{run.run_id}/approval", json=body)
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "STALE_RUN_VERSION"
        assert snapshot(v2.path) == stable
        next_run = advance(v2, RunState.AwaitingApproval, Risk.SIDE_EFFECT)
    else:
        next_run = run
    healthy = http_server.post(f"/api/runs/{next_run.run_id}/approval", json=body)
    assert healthy.status_code == 200
    assert v2.get(next_run.run_id).state == RunState.Approved
    record_fact(
        "v2-run-http",
        scenario=case,
        expected_status=expected,
        actual_status=response.status_code,
        safe_code="DATABASE_UNAVAILABLE" if expected == 503 else None,
        state=after.state.value,
        version=after.version,
        recovery_status=healthy.status_code,
        auto_retry=False,
        result="PASS",
    )


def test_real_http_domain_conflict_survives_cleanup(v2, faults, monkeypatch, http_server):
    run = advance(v2, RunState.AwaitingApproval, Risk.SIDE_EFFECT)
    before = snapshot(v2.path)
    with monkeypatch.context() as patcher:
        opened = faults(Fault(rollback=True, arm_on_open=True), patcher)
        response = http_server.post(
            f"/api/runs/{run.run_id}/approval", json={"decision": "APPROVE", "expected_version": 3}
        )
        assert response.status_code == 409
        assert response.json() == {
            "run_id": run.run_id,
            "error": {
                "code": "STALE_RUN_VERSION",
                "message": RUN_MESSAGES[RunErrorCode.STALE_RUN_VERSION],
            },
        }
        released(v2.path, opened, rollback_attempts=1)
    assert snapshot(v2.path) == before
    record_fact(
        "v2-run-http",
        scenario="domain-conflict-rollback",
        expected_status=409,
        actual_status=response.status_code,
        safe_code="STALE_RUN_VERSION",
        state=run.state.value,
        version=run.version,
        result="PASS",
    )
