"""Prompt 5: real killed processes and durable effects, not mock recovery proof."""

import asyncio
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from threading import Barrier
from uuid import uuid4

import pytest
from action_helpers import cmd
from execution_process_helpers import OwnedWorker, record_fact
from run_helpers import advance, database_rows
from test_service_demo_adapter import downstream, invoke

from agentic_intent_gate.adapters import sqlite_actions
from agentic_intent_gate.domain.action import ActionCode, ActionError, ActionState, safe_result
from agentic_intent_gate.domain.run import RunState


def authorize(e):
    e.action = e.repo.authorize(cmd(e.action), e.principal, e.policy).action


def execute(worker, e, key):
    return worker.request(
        f"/api/actions/{e.action.action_id}/execute", body={"expected_action_version": 2}, key=key
    )


def call_count(e):
    path = e.tmp_path / "calls.jsonl"
    if not path.exists():
        return 0
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert all(row["call"] == 1 for row in rows)
    return len(rows)


def snapshot(e):
    return e.ledger(), database_rows(e.runs.path), downstream(e.demo_path)


def schema_snapshot(e):
    result = []
    for path in (e.runs.path, e.demo_path):
        with closing(sqlite3.connect(path)) as connection:
            result.append(
                (
                    connection.execute("PRAGMA user_version").fetchone(),
                    connection.execute("SELECT * FROM sqlite_schema ORDER BY name").fetchall(),
                )
            )
            assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            assert not connection.execute("PRAGMA foreign_key_check").fetchall()
    return result


def assert_pure_reads(worker, e):
    before = snapshot(e)
    for _ in range(3):
        assert worker.request(f"/api/actions/{e.action.action_id}")[0] == 200
        assert worker.request(f"/api/runs/{e.run.run_id}/actions")[0] == 200
        assert worker.request(f"/api/runs/{e.run.run_id}")[0] == 200
    assert snapshot(e) == before


@pytest.mark.parametrize(
    "window,before,after,calls,effects,events",
    [
        ("A", "Authorized", "Authorized", 0, 0, 2),
        ("B", "Executing", "Unknown", 0, 0, 4),
        ("C", "Executing", "Unknown", 1, 0, 4),
        ("D", "Executing", "Unknown", 1, 1, 4),
        ("E", "Executing", "Unknown", 1, 0, 4),
        ("F", "Succeeded", "Succeeded", 1, 1, 4),
        ("G", "Unknown", "Unknown", 1, 1, 4),
    ],
)
def test_real_termination_crash_windows(demo_env, window, before, after, calls, effects, events):
    e, key = demo_env, str(uuid4())
    authorize(e)
    if window == "E":
        with closing(sqlite3.connect(e.demo_path)) as connection:
            connection.execute("UPDATE resource SET execution_scope=?", (str(uuid4()),))
            connection.commit()
    run_before = database_rows(e.runs.path)
    schema_before = schema_snapshot(e)
    with OwnedWorker(e, window=window) as worker, ThreadPoolExecutor(1) as pool:
        request = pool.submit(execute, worker, e, key)
        worker.await_phase(window)
        assert e.repo.get(e.action.action_id).state.value == before
        assert call_count(e) == calls
        assert len(downstream(e.demo_path)["notes"]) == effects
        worker.kill_at(window)
        assert request.result(timeout=12)[0] == 0  # No HTTP response delivered.
    with OwnedWorker(e, recover_repetitions=10) as restarted:
        action = e.repo.get(e.action.action_id)
        assert action.state.value == after and len(e.ledger()["action_events"]) == events
        assert len(e.ledger()["action_attempts"]) == (0 if window == "A" else 1)
        assert len(downstream(e.demo_path)["notes"]) == effects
        assert len(downstream(e.demo_path)["receipts"]) == effects
        assert call_count(e) == calls and database_rows(e.runs.path) == run_before
        assert_pure_reads(restarted, e)
        if window != "A":
            stable = snapshot(e)
            assert execute(restarted, e, key)[0] == 200
            assert (
                restarted.request(
                    f"/api/actions/{e.action.action_id}/execute",
                    body={"expected_action_version": 3},
                    key=key,
                )[0]
                == 409
            )
            assert execute(restarted, e, str(uuid4()))[0] == 409
            assert snapshot(e) == stable and call_count(e) == calls
    stable = snapshot(e)
    # A SECOND actual interpreter restart is also convergent.
    with OwnedWorker(e, recover_repetitions=2):
        assert snapshot(e) == stable and call_count(e) == calls
        assert schema_snapshot(e) == schema_before
    record_fact(
        "crash-window",
        window=window,
        persisted_before=before,
        recovered_state=action.state.value,
        calls=call_count(e),
        notes=len(downstream(e.demo_path)["notes"]),
        receipts=len(downstream(e.demo_path)["receipts"]),
        attempts=len(e.ledger()["action_attempts"]),
        events=events,
        schema_unchanged=True,
        convergence_repetitions=[1, 2, 10],
    )


@pytest.mark.parametrize("operation", ["admit", "authorize"])
def test_real_termination_atomic_admission_authorization(demo_env, operation):
    e, key = demo_env, str(uuid4())
    if operation == "admit":
        run = advance(e.runs, RunState.Approved, e.run.plan.risk_class)
        path, body = f"/api/runs/{run.run_id}/actions", {"expected_run_version": 5}
    else:
        path = f"/api/actions/{e.action.action_id}/authorize"
        body = {"expected_action_version": 1}
    before = snapshot(e)
    phase = "H-" + operation
    with OwnedWorker(e, window=phase) as worker, ThreadPoolExecutor(1) as pool:
        request = pool.submit(worker.request, path, body=body, key=key)
        worker.await_phase(phase)
        worker.kill_at(phase)
        assert request.result(timeout=12)[0] == 0
    with OwnedWorker(e) as restarted:
        assert snapshot(e) == before and call_count(e) == 0
        assert restarted.request(path, body=body, key=key)[0] in {200, 201}
        stable = snapshot(e)
        assert restarted.request(path, body=body, key=key)[0] == 200
        assert snapshot(e) == stable


@pytest.mark.parametrize("operation", ["admit", "authorize"])
def test_committed_command_lost_response_restart_idempotency(demo_env, operation):
    e, key = demo_env, str(uuid4())
    if operation == "admit":
        run = advance(e.runs, RunState.Approved, e.run.plan.risk_class)
        path, field, version = f"/api/runs/{run.run_id}/actions", "expected_run_version", 5
    else:
        path, field, version = (
            f"/api/actions/{e.action.action_id}/authorize",
            "expected_action_version",
            1,
        )
    phase = operation + "-response-loss"
    with OwnedWorker(e, window=phase) as worker, ThreadPoolExecutor(1) as pool:
        request = pool.submit(worker.request, path, body={field: version}, key=key)
        worker.await_phase(phase)
        stable = snapshot(e)
        worker.kill_at(phase)
        assert request.result(timeout=12)[0] == 0
    for _ in range(2):
        with OwnedWorker(e) as restarted:
            response = restarted.request(path, body={field: version}, key=key)
            assert response[0] == 200 and response[1]["replayed"]
            assert restarted.request(path, body={field: version + 1}, key=key)[0] == 409
            assert snapshot(e) == stable and call_count(e) == 0


def test_active_owner_blocks_competing_startup_and_recovery(demo_env):
    e, key = demo_env, str(uuid4())
    authorize(e)
    with OwnedWorker(e, window="active") as active, ThreadPoolExecutor(1) as pool:
        request = pool.submit(execute, active, e, key)
        active.await_phase("active")
        stable = snapshot(e)
        with OwnedWorker(e, ready=False) as competitor:
            competitor.await_phase("denied")
            assert snapshot(e) == stable
            assert e.repo.get(e.action.action_id).state is ActionState.Executing
            active.kill_at("active")
            assert request.result(timeout=12)[0] == 0
            competitor.process.wait(timeout=8)
            assert competitor.process.returncode == 2
    assert (e.runs.path.parent / (e.runs.path.name + ".owner")).exists()
    with OwnedWorker(e):
        assert e.repo.get(e.action.action_id).state is ActionState.Unknown
        assert call_count(e) == 1 and not downstream(e.demo_path)["notes"]


def test_two_simultaneous_process_startups_have_one_owner(demo_env):
    e = demo_env
    start = Barrier(2)

    def spawn(_):
        start.wait(timeout=3)
        return OwnedWorker(e, ready=False)

    with ThreadPoolExecutor(2) as pool:
        workers = list(pool.map(spawn, range(2)))
    try:
        phases = []
        for worker in workers:
            event = worker.events.get(timeout=12)
            assert event["nonce"] == worker.nonce and event["pid"] == worker.process.pid
            if event["phase"] == "owner":
                worker.await_phase("ready")
                phases.append("ready")
            else:
                assert event["phase"] == "denied"
                phases.append("denied")
        assert sorted(phases) == ["denied", "ready"]
        assert call_count(e) == 0
    finally:
        for worker in workers:
            worker.close()


@pytest.mark.parametrize("same_key", [True, False])
def test_twenty_real_http_contenders_one_durable_claim(demo_env, same_key):
    e, key = demo_env, str(uuid4())
    authorize(e)
    start = Barrier(20)
    with OwnedWorker(e, contenders=20) as worker:

        def contend(_):
            start.wait(timeout=5)
            return execute(worker, e, key if same_key else str(uuid4()))

        with ThreadPoolExecutor(20) as pool:
            responses = list(pool.map(contend, range(20)))
        assert sum(status == 200 and not body["replayed"] for status, body in responses) == 1
        assert all(status in ({200, 202} if same_key else {200, 409}) for status, _ in responses)
        assert e.repo.get(e.action.action_id).state is ActionState.Succeeded
        assert call_count(e) == len(e.ledger()["action_attempts"]) == 1
        assert len(e.ledger()["action_events"]) == 4
        assert (
            len(downstream(e.demo_path)["notes"]) == len(downstream(e.demo_path)["receipts"]) == 1
        )
        stable = snapshot(e)
        for version in (1, 2, 3, 4):
            assert (
                worker.request(
                    f"/api/actions/{e.action.action_id}/execute",
                    body={"expected_action_version": version},
                    key=str(uuid4()),
                )[0]
                == 409
            )
        assert snapshot(e) == stable and call_count(e) == 1


@pytest.mark.parametrize(
    "window", ["downstream-response-loss", "adapter-exception", "timeout", "cancel"]
)
def test_real_effect_uncertainty_terminal_unknown(demo_env, window):
    e, key = demo_env, str(uuid4())
    authorize(e)
    with OwnedWorker(e, window=window) as worker, ThreadPoolExecutor(1) as pool:
        request = pool.submit(execute, worker, e, key)
        if window == "cancel":
            worker.await_phase("cancel")
            worker.control("cancel")
        response = request.result(timeout=12)
        assert response[0] == (500 if window == "cancel" else 200)
        assert e.repo.get(e.action.action_id).state is ActionState.Unknown
        assert call_count(e) == 1 and len(downstream(e.demo_path)["notes"]) == 1
        assert_pure_reads(worker, e)
    with OwnedWorker(e) as restarted:
        stable = snapshot(e)
        assert execute(restarted, e, key)[0] == 200
        assert execute(restarted, e, str(uuid4()))[0] == 409
        assert snapshot(e) == stable and call_count(e) == 1


@pytest.mark.parametrize(
    "window", ["result-write-fault", "result-commit-fault", "result-close", "result-after-commit"]
)
def test_result_storage_fault_honest_error_then_restart(demo_env, window):
    e, key = demo_env, str(uuid4())
    authorize(e)
    with OwnedWorker(e, window=window) as worker:
        response = execute(worker, e, key)
        assert response[0] == 503 and "action" not in response[1]
        state = ActionState.Succeeded if window == "result-after-commit" else ActionState.Executing
        assert e.repo.get(e.action.action_id).state is state
        assert call_count(e) == 1 and len(downstream(e.demo_path)["notes"]) == 1
        assert_pure_reads(worker, e)
    with OwnedWorker(e) as restarted:
        final = ActionState.Succeeded if window == "result-after-commit" else ActionState.Unknown
        assert e.repo.get(e.action.action_id).state is final
        stable = snapshot(e)
        assert execute(restarted, e, key)[0] == 200
        assert snapshot(e) == stable and call_count(e) == 1


@pytest.mark.parametrize("window", ["B", "F"])
def test_retired_binding_historical_read_recovery_no_execution(demo_env, window):
    e, key = demo_env, str(uuid4())
    authorize(e)
    with OwnedWorker(e, window=window) as worker, ThreadPoolExecutor(1) as pool:
        request = pool.submit(execute, worker, e, key)
        worker.await_phase(window)
        worker.kill_at(window)
        assert request.result(timeout=12)[0] == 0
    retained = e.repo.get(e.action.action_id).spec
    with OwnedWorker(e, retired=True) as restarted:
        assert e.repo.get(e.action.action_id).spec == retained
        assert_pure_reads(restarted, e)
        stable = snapshot(e)
        assert execute(restarted, e, key)[0] == 409
        assert snapshot(e) == stable and call_count(e) == (0 if window == "B" else 1)


def test_run_and_action_recovery_are_independent(demo_env):
    e = demo_env
    authorize(e)
    interrupted = advance(e.runs, RunState.PlanGenerating)
    approved = e.runs.get(e.run.run_id)
    e.repo.claim(cmd(e.action), e.principal, e.policy)
    with OwnedWorker(e, recover_repetitions=10):
        assert e.runs.get(interrupted.run_id).state is RunState.PlanRejected
        assert e.runs.get(e.run.run_id) == approved
        assert e.repo.get(e.action.action_id).state is ActionState.Unknown
        assert call_count(e) == 0 and not downstream(e.demo_path)["notes"]
    stable = snapshot(e)
    with OwnedWorker(e):
        assert snapshot(e) == stable


async def test_blocking_sqlite_soft_timeout_is_not_preemptive(execution_env):
    e = execution_env
    blocker = sqlite3.connect(e.demo_path, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    e.gateway.tool_timeout_seconds = 0.01
    ticks = []

    async def heartbeat():
        await asyncio.sleep(0.001)
        ticks.append(time.monotonic())

    heartbeat_task = asyncio.create_task(heartbeat())
    started = time.monotonic()
    try:
        result = await e.gateway.execute(cmd(e.action), e.principal)
        elapsed = time.monotonic() - started
        await heartbeat_task
    finally:
        blocker.rollback()
        blocker.close()
    assert result.action.state is ActionState.Unknown and e.calls == 1
    assert elapsed >= 0.15 and ticks[0] - started >= 0.15
    assert not downstream(e.demo_path)["notes"]
    record_fact(
        "blocking-timeout",
        tool_budget_seconds=0.01,
        elapsed_seconds=elapsed,
        heartbeat_lag_seconds=ticks[0] - started,
        state=result.action.state.value,
        notes=0,
    )


def test_real_busy_result_transaction_never_fabricates_terminal(demo_env):
    e, key = demo_env, str(uuid4())
    authorize(e)
    with OwnedWorker(e, window="result-busy") as worker, ThreadPoolExecutor(1) as pool:
        request = pool.submit(execute, worker, e, key)
        worker.await_phase("result-busy")
        with closing(sqlite3.connect(e.runs.path, isolation_level=None)) as blocker:
            blocker.execute("BEGIN IMMEDIATE")
            worker.control("release")
            response = request.result(timeout=12)
            assert response[0] == 503
            assert e.repo.get(e.action.action_id).state is ActionState.Executing
            blocker.rollback()
        assert call_count(e) == 1 and len(downstream(e.demo_path)["notes"]) == 1
    with OwnedWorker(e) as restarted:
        assert e.repo.get(e.action.action_id).state is ActionState.Unknown
        stable = snapshot(e)
        assert execute(restarted, e, key)[0] == 200
        assert snapshot(e) == stable


async def test_expired_dispatch_budget_creates_no_downstream_effect(execution_env):
    e = execution_env
    result = await e.gateway.execute(
        cmd(e.action), e.principal, deadline=asyncio.get_running_loop().time() - 1
    )
    assert result.action.state is ActionState.Unknown
    assert not downstream(e.demo_path)["notes"] and not downstream(e.demo_path)["receipts"]


@pytest.mark.parametrize("same_payload", [True, False])
def test_downstream_independent_connections_concurrent_dedup_reopen(demo_env, same_payload):
    e = demo_env
    first = asyncio.run(invoke(e))
    spec = e.action.spec
    # The adapter port is tested directly; this is NOT permission to retry a gate Action.
    from agentic_intent_gate.ports.tool_executor import ExecutionSpec, Outcome

    narrow = ExecutionSpec.from_compiled(spec)
    changed = replace(narrow, arguments={"service_id": "service-demo", "note": "different"})
    start = Barrier(20)

    def contender(_):
        start.wait(timeout=5)
        return asyncio.run(invoke(e, narrow if same_payload else changed))

    with ThreadPoolExecutor(20) as pool:
        results = list(pool.map(contender, range(20)))
    confirmed = first.category if same_payload else Outcome.CONFIRMED_FAILURE
    assert any(r.category is confirmed for r in results)
    # The frozen 250ms busy bound may conservatively return Unknown under contention;
    # it never grants a retry or proves payload rejection without a confirmed lookup.
    assert all(r.category in {confirmed, Outcome.UNKNOWN_OUTCOME} for r in results)
    if same_payload:
        assert all(r == first for r in results if r.category is confirmed)
    assert len(downstream(e.demo_path)["notes"]) == len(downstream(e.demo_path)["receipts"]) == 1
    assert asyncio.run(invoke(e)) == first


def test_wrong_attempt_and_late_duplicate_completion_after_process_recovery(demo_env):
    e = demo_env
    authorize(e)
    claim = e.repo.claim(cmd(e.action), e.principal, e.policy)
    with pytest.raises(ActionError):
        e.repo.finish(e.action.action_id, str(uuid4()), safe_result(ActionState.Unknown))
    with OwnedWorker(e):
        stable = snapshot(e)
        for result in (
            safe_result(ActionState.Unknown),
            safe_result(ActionState.Succeeded, str(uuid4())),
        ):
            with pytest.raises(ActionError):
                e.repo.finish(e.action.action_id, claim.attempt_id, result)
        assert snapshot(e) == stable and call_count(e) == 0


@pytest.mark.parametrize("changed_payload", [False, True])
def test_downstream_process_concurrency_and_actual_interpreter_reopen(demo_env, changed_payload):
    e = demo_env
    gate_before = e.ledger()
    if changed_payload:
        asyncio.run(invoke(e))
    with (
        OwnedWorker(
            e, ready=False, adapter_port_test=True, changed_payload=changed_payload
        ) as first,
        OwnedWorker(
            e, ready=False, adapter_port_test=True, changed_payload=changed_payload
        ) as second,
    ):
        first.await_phase("adapter-port-ready")
        second.await_phase("adapter-port-ready")
        first.control("release")
        second.control("release")
        results = [child.await_phase("adapter-port-result") for child in (first, second)]
        for child in (first, second):
            if child.process.poll() is None:
                child.kill_at("adapter-port-result")
            else:
                assert child.process.returncode == 0
    if changed_payload:
        assert all(row["reason"] == "PAYLOAD_CONFLICT" for row in results)
    else:
        assert all(row["category"] == "SUCCESS" for row in results)
        assert results[0]["receipt"] == results[1]["receipt"]
    receipt = downstream(e.demo_path)["receipts"][0][2]
    with OwnedWorker(e, ready=False, adapter_port_test=True) as reopened:
        reopened.await_phase("adapter-port-ready")
        reopened.control("release")
        assert reopened.await_phase("adapter-port-result")["receipt"] == receipt
        if reopened.process.poll() is None:
            reopened.kill_at("adapter-port-result")
        else:
            assert reopened.process.returncode == 0
    assert e.ledger() == gate_before
    assert len(downstream(e.demo_path)["notes"]) == len(downstream(e.demo_path)["receipts"]) == 1


def test_capability_never_optional_after_restart(demo_env):
    e = demo_env
    authorize(e)
    for _ in range(2):
        with OwnedWorker(e) as worker:
            stable = snapshot(e)
            assert (
                worker.request(f"/api/actions/{e.action.action_id}", authenticated=False)[0] == 401
            )
            assert (
                worker.request(
                    f"/api/actions/{e.action.action_id}/execute",
                    body={"expected_action_version": 2},
                    key=str(uuid4()),
                    authenticated=False,
                )[0]
                == 401
            )
            assert snapshot(e) == stable and call_count(e) == 0


@pytest.mark.parametrize("stage", ["in_transaction", "rollback", "close"])
def test_cleanup_fault_still_closes_and_preserves_safe_store_error(demo_env, monkeypatch, stage):
    e, opened = demo_env, []
    before = snapshot(e)

    class CleanupFault(sqlite3.Connection):
        cleanup_mode = False
        close_attempted = False

        @property
        def in_transaction(self):
            if self.cleanup_mode and stage == "in_transaction":
                raise sqlite3.ProgrammingError("Owned disconnected connection")
            return super().in_transaction

        def rollback(self):
            if self.cleanup_mode and stage == "rollback":
                raise sqlite3.OperationalError("Owned rollback interruption")
            return super().rollback()

        def close(self):
            self.close_attempted = True
            super().close()
            if self.cleanup_mode and stage == "close":
                raise sqlite3.OperationalError("Owned close acknowledgement loss")

    def open_fault(path):
        connection = sqlite3.connect(path, isolation_level=None, factory=CleanupFault)
        opened.append(connection)
        return connection

    with monkeypatch.context() as patch:
        patch.setattr(sqlite_actions, "open_existing", open_fault)
        with pytest.raises(ActionError) as error, e.repo._transaction(write=True) as connection:
            connection.cleanup_mode = True
            raise sqlite3.OperationalError("Owned primary write fault")
        assert error.value.code is ActionCode.DATABASE_UNAVAILABLE
    assert len(opened) == 1 and opened[0].close_attempted
    with pytest.raises(sqlite3.ProgrammingError):
        opened[0].execute("SELECT 1")
    assert snapshot(e) == before
