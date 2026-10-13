import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Barrier, Event, Lock
from uuid import uuid4

import pytest
from action_helpers import cmd, inject_fault
from test_service_demo_adapter import downstream

import agentic_intent_gate.infrastructure.tools.service_demo as service_demo
from agentic_intent_gate.application.action_execution import ActionExecutionGateway
from agentic_intent_gate.domain.action import ActionCode, ActionError, ActionState
from agentic_intent_gate.infrastructure.persistence.sqlite_action_execution import safe_result
from agentic_intent_gate.ports.tool_executor import Outcome, ToolOutcome


async def test_controlled_execution_reopen_and_lost_response_replay(execution_env):
    e = execution_env
    request = cmd(e.action)
    result = await e.gateway.execute(request, e.principal)
    assert result.action.state is ActionState.Succeeded and result.action.version == 4
    assert e.calls == 1 and e.runs.get(e.run.run_id).version == 5
    assert len(downstream(e.demo_path)["notes"]) == len(e.ledger()["action_attempts"]) == 1
    assert len(e.ledger()["action_events"]) == 4
    before = e.ledger(), downstream(e.demo_path)
    assert (await e.gateway.execute(request, e.principal)).replayed
    assert e.repo.get(e.action.action_id) == result.action
    assert before == (e.ledger(), downstream(e.demo_path)) and e.calls == 1
    with pytest.raises(ActionError):
        await e.gateway.execute(cmd(result.action), e.principal)
    assert e.calls == 1


@pytest.mark.parametrize("same_key", [True, False])
def test_twenty_concurrent_execute_requests_one_claim_call_effect(
    execution_env, same_key, monkeypatch
):
    e = execution_env
    barrier, key = Barrier(20), str(uuid4())
    original_claim = e.repo.claim
    completed, claim_lock = 0, Lock()
    e.claims_drained = Event()
    e.claim_wait_lock, e.claim_listener = claim_lock, None

    def tracked_claim(*args, **kwargs):
        nonlocal completed
        try:
            return original_claim(*args, **kwargs)
        finally:
            with claim_lock:
                completed += 1
                if completed == 20:
                    e.claims_drained.set()
                    if e.claim_listener is not None:
                        loop, event = e.claim_listener
                        if not loop.is_closed():
                            loop.call_soon_threadsafe(event.set)

    monkeypatch.setattr(e.repo, "claim", tracked_claim)

    def call(index):
        barrier.wait(timeout=5)
        try:
            return asyncio.run(
                e.gateway.execute(cmd(e.action, key=key if same_key else str(uuid4())), e.principal)
            )
        except ActionError:
            return None

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(call, range(20)))
    assert len([r for r in results if r and not r.replayed]) == 1
    assert e.calls == 1
    assert e.repo.get(e.action.action_id).state is ActionState.Succeeded
    assert len(e.ledger()["action_attempts"]) == len(downstream(e.demo_path)["notes"]) == 1
    assert len(downstream(e.demo_path)["receipts"]) == 1
    assert len(e.ledger()["action_events"]) == 4


@pytest.mark.parametrize(
    "kind", ["exception", "timeout", "bad_success", "bad_failure", "raw", "unknown"]
)
async def test_uncertain_or_malformed_dispatch_evidence_is_unknown(
    execution_env, monkeypatch, kind
):
    e = execution_env

    async def uncertain(spec, key, context):
        e.calls += 1
        if kind == "exception":
            raise RuntimeError("secret SQL path bearer detail")
        if kind == "timeout":
            await asyncio.sleep(0.1)
        if kind == "bad_success":
            return ToolOutcome(
                Outcome.SUCCESS,
                key,
                spec.execution_scope,
                spec.target,
                spec.payload_digest(),
                "not-a-receipt",
            )
        if kind == "bad_failure":
            return ToolOutcome(
                Outcome.CONFIRMED_FAILURE,
                key,
                spec.execution_scope,
                spec.target,
                spec.payload_digest(),
                no_effect_reason="HTTP_500",
            )
        if kind == "raw":
            return {"success": True, "credential": "secret"}
        return ToolOutcome(Outcome.UNKNOWN_OUTCOME)

    monkeypatch.setattr(e.executor, "execute", uncertain)
    e.gateway = ActionExecutionGateway(e.repo, e.policy, e.registry, tool_timeout_seconds=0.01)
    request = cmd(e.action)
    result = await e.gateway.execute(request, e.principal)
    assert result.action.state is ActionState.Unknown and e.calls == 1
    assert result.action.result == safe_result(ActionState.Unknown)
    assert "secret" not in str(e.ledger())
    assert (await e.gateway.execute(request, e.principal)).replayed and e.calls == 1
    with pytest.raises(ActionError):
        await e.gateway.execute(cmd(result.action), e.principal)


async def test_authoritative_no_effect_is_failed(execution_env):
    e = execution_env
    with closing(sqlite3.connect(e.demo_path)) as connection:
        connection.execute("UPDATE resource SET execution_scope=?", (str(uuid4()),))
        connection.commit()
    result = await e.gateway.execute(cmd(e.action), e.principal)
    assert result.action.state is ActionState.Failed and not downstream(e.demo_path)["notes"]


@pytest.mark.parametrize(
    "stage",
    [
        "INSERT INTO action_attempts",
        "UPDATE actions",
        "INSERT INTO action_events",
        "INSERT INTO action_command_receipts",
        "COMMIT",
    ],
)
async def test_claim_fault_never_yields_dispatch_permission(execution_env, monkeypatch, stage):
    e = execution_env
    before = e.ledger()
    with monkeypatch.context() as patch:
        inject_fault(patch, stage)
        with pytest.raises(ActionError):
            await e.gateway.execute(cmd(e.action), e.principal)
    assert e.ledger() == before and e.calls == 0
    assert not downstream(e.demo_path)["notes"]


async def test_unconfirmed_committed_claim_does_not_dispatch(execution_env, monkeypatch):
    e = execution_env
    request = cmd(e.action)
    with monkeypatch.context() as patch:
        inject_fault(patch, "COMMIT", after_commit=True)
        with pytest.raises(ActionError):
            await e.gateway.execute(request, e.principal)
    assert e.repo.get(e.action.action_id).state is ActionState.Executing and e.calls == 0
    assert (await e.gateway.execute(request, e.principal)).replayed and e.calls == 0
    assert e.repo.recover(owners_stopped=True) == 1
    assert e.repo.get(e.action.action_id).state is ActionState.Unknown


async def test_result_commit_response_lost_preserves_success_without_resend(
    execution_env, monkeypatch
):
    e = execution_env
    request = cmd(e.action)
    original = e.executor.execute
    with monkeypatch.context() as patch:

        async def effect_then_lost_commit(*args):
            outcome = await original(*args)
            inject_fault(patch, "COMMIT", after_commit=True)
            return outcome

        patch.setattr(e.executor, "execute", effect_then_lost_commit)
        with pytest.raises(ActionError):
            await e.gateway.execute(request, e.principal)
    result = e.repo.get(e.action.action_id)
    assert result.state is ActionState.Succeeded and result.version == 4
    assert len(e.ledger()["action_events"]) == 4
    before = e.ledger(), downstream(e.demo_path)
    assert (await e.gateway.execute(request, e.principal)).replayed
    assert e.calls == 1 and before == (e.ledger(), downstream(e.demo_path))


async def test_unconfirmed_downstream_commit_has_effect_but_gate_unknown(
    execution_env, monkeypatch
):
    e = execution_env

    class LostCommit(sqlite3.Connection):
        def commit(self):
            super().commit()
            raise sqlite3.OperationalError("downstream commit response lost")

    def open_lost(path):
        connection = sqlite3.connect(path, isolation_level=None, factory=LostCommit)
        connection.row_factory = sqlite3.Row
        return connection

    monkeypatch.setattr(service_demo, "open_existing", open_lost)
    request = cmd(e.action)
    result = await e.gateway.execute(request, e.principal)
    assert result.action.state is ActionState.Unknown and e.calls == 1
    assert len(downstream(e.demo_path)["notes"]) == len(downstream(e.demo_path)["receipts"]) == 1
    assert (await e.gateway.execute(request, e.principal)).replayed and e.calls == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("attempt_id", str(uuid4())),
        ("authorization_id", str(uuid4())),
        ("subject_id", "other"),
        ("spec_digest", "0" * 64),
        ("policy_revision", "different"),
        ("claimed_version", 2),
        ("claimed_at", "2020-01-01T00:00:00.000000Z"),
    ],
)
def test_attempt_tamper_blocks_result_read_recovery(execution_env, field, value):
    e = execution_env
    claim = e.repo.claim(cmd(e.action), e.principal, e.policy)
    with closing(sqlite3.connect(e.runs.path)) as connection:
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(f"UPDATE action_attempts SET {field}=?", (value,))
        connection.commit()
    before = e.ledger()
    with pytest.raises(ActionError):
        e.repo.finish(e.action.action_id, claim.attempt_id, safe_result(ActionState.Unknown))
    with pytest.raises(ActionError):
        e.repo.recover(owners_stopped=True)
    assert e.ledger() == before and e.calls == 0


@pytest.mark.parametrize("version", [1, 3, 4])
async def test_stale_execute_cannot_call(execution_env, version):
    e = execution_env
    before = e.ledger()
    with pytest.raises(ActionError) as exc:
        await e.gateway.execute(cmd(e.action, version), e.principal)
    assert exc.value.code is ActionCode.STALE_VERSION
    assert e.calls == 0 and e.ledger() == before


async def test_changed_body_key_after_success_conflicts_without_call(execution_env):
    e = execution_env
    request = cmd(e.action)
    await e.gateway.execute(request, e.principal)
    before = e.ledger()
    with pytest.raises(ActionError) as exc:
        await e.gateway.execute(cmd(e.action, version=4, key=request.idempotency_key), e.principal)
    assert exc.value.code is ActionCode.IDEMPOTENCY_CONFLICT
    assert e.calls == 1 and e.ledger() == before


@pytest.mark.parametrize("stage", ["UPDATE actions", "INSERT INTO action_events", "COMMIT"])
async def test_result_failure_after_effect_never_false_failed_or_retries(
    execution_env, monkeypatch, stage
):
    e = execution_env
    original = e.executor.execute

    async def effect_then_fault(*args):
        outcome = await original(*args)
        inject_fault(monkeypatch, stage)
        return outcome

    with monkeypatch.context() as patch:
        monkeypatch = patch
        patch.setattr(e.executor, "execute", effect_then_fault)
        with pytest.raises(ActionError) as exc:
            await e.gateway.execute(cmd(e.action), e.principal)
        assert exc.value.code is ActionCode.DATABASE_UNAVAILABLE
    assert e.calls == 1 and len(downstream(e.demo_path)["notes"]) == 1
    assert e.repo.get(e.action.action_id).state is ActionState.Executing
    assert len(e.ledger()["action_events"]) == 3
    e.repo.recover(owners_stopped=True)
    assert e.repo.get(e.action.action_id).state is ActionState.Unknown


async def test_cancellation_finishes_unknown_without_detached_work(execution_env, monkeypatch):
    e = execution_env
    entered = asyncio.Event()
    stopped = []

    async def waiting(*args):
        e.calls += 1
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.append(True)

    monkeypatch.setattr(e.executor, "execute", waiting)
    task = asyncio.create_task(e.gateway.execute(cmd(e.action), e.principal))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped and e.repo.get(e.action.action_id).state is ActionState.Unknown
    assert e.calls == 1 and not downstream(e.demo_path)["notes"]


def test_stale_or_other_attempt_completion_cannot_overwrite(execution_env):
    e = execution_env
    claim = e.repo.claim(cmd(e.action), e.principal, e.policy)
    with pytest.raises(ActionError):
        e.repo.finish(e.action.action_id, str(uuid4()), safe_result(ActionState.Unknown))
    e.repo.recover(owners_stopped=True)
    before = e.ledger()
    with pytest.raises(ActionError):
        e.repo.finish(
            e.action.action_id, claim.attempt_id, safe_result(ActionState.Succeeded, str(uuid4()))
        )
    assert e.ledger() == before
