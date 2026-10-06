"""Durable provider barriers, request isolation, safe failures and cancellation."""

import asyncio
import json
from collections import Counter
from contextlib import closing
from dataclasses import replace

import pytest
from run_helpers import database_rows, plan_for

from agentic_intent_gate.application.generate_plan import PlanningService
from agentic_intent_gate.application.runs import RunService
from agentic_intent_gate.domain.contracts import (
    ErrorCode,
    Intent,
    PlanningResult,
    PlanningState,
    Risk,
)
from agentic_intent_gate.domain.run import RunErrorCode, RunState, RunStoreError
from agentic_intent_gate.domain.validation import ValidationFailure
from agentic_intent_gate.ports.model_provider import ModelProviderError


@pytest.mark.parametrize("risk", list(Risk))
async def test_provider_observes_committed_barriers(run_repository, validator, risk):
    plan = plan_for(validator, risk)

    class BarrierProvider:
        calls = 0

        async def generate_plan(self, intent, **kwargs):
            self.calls += 1
            # An independent connection sees both committed barriers, and can take a write lock.
            rows = database_rows(run_repository.path)
            assert len(rows["runs"]) == 1 and rows["runs"][0][2] == "PlanGenerating"
            assert rows["runs"][0][5] == 2
            assert [r[3] for r in rows["audit_events"]] == [
                "RUN_CREATED",
                "PLAN_GENERATION_STARTED",
            ]
            import sqlite3

            with closing(sqlite3.connect(run_repository.path, timeout=0.01)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.rollback()
            return json.dumps(plan.to_dict())

    provider = BarrierProvider()
    service = RunService(run_repository, PlanningService(provider, validator))
    run = await service.create(Intent("Read or propose a write"))
    assert provider.calls == 1
    assert run.state == (RunState.Ready if risk == Risk.READ_ONLY else RunState.AwaitingApproval)
    assert run.version == 4 and run.plan == plan and run.risk_class == risk
    assert run_repository.get(run.run_id) == run
    assert len(database_rows(run_repository.path)["audit_events"]) == 4


@pytest.mark.parametrize("kind", ["json", "risk", "target", "revision", "tool"])
async def test_invalid_provider_output_never_persists_as_plan(
    run_repository, validator, provider_factory, kind
):
    plan = plan_for(validator, Risk.APPROVAL_REQUIRED).to_dict()
    if kind == "risk":
        plan["risk_class"] = "READ_ONLY"
    elif kind == "target":
        plan["target"] = plan["steps"][0]["arguments"]["service_id"] = "production"
    elif kind == "revision":
        plan["steps"][0]["arguments"]["expected_revision"] = 2
    elif kind == "tool":
        plan["steps"][0]["tool_name"] = "shell"
    raw = "private raw malformed response" if kind == "json" else json.dumps(plan)
    provider = provider_factory(raw=raw)
    run = await RunService(run_repository, PlanningService(provider, validator)).create(
        Intent("Plan")
    )
    assert provider.calls == 1 and run.state == RunState.PlanRejected
    assert run.version == 3 and run.plan is None and run.risk_class is None
    assert run.error.code == ErrorCode.PLAN_VALIDATION_ERROR
    assert "private raw" not in str(database_rows(run_repository.path))


@pytest.mark.parametrize(
    "code",
    [
        ErrorCode.MODEL_TIMEOUT,
        ErrorCode.MODEL_UNAVAILABLE,
        ErrorCode.MODEL_RESPONSE_ERROR,
        ErrorCode.INTERNAL_ERROR,
    ],
)
async def test_provider_failures_are_durable_safe_errors(
    run_repository, validator, provider_factory, code
):
    error = (
        RuntimeError("private provider exception")
        if code == ErrorCode.INTERNAL_ERROR
        else ModelProviderError(code)
    )
    provider = provider_factory(error=error)
    run = await RunService(run_repository, PlanningService(provider, validator)).create(
        Intent("Plan")
    )
    assert run.error.code == code and run.state == RunState.PlanRejected and run.version == 3
    assert run_repository.get(run.run_id) == run and provider.calls == 1
    assert "private provider" not in str(database_rows(run_repository.path))


async def test_actual_async_timeout_stops_awaiting_and_persists(run_repository, validator):
    class SlowProvider:
        calls = 0
        cancelled = False

        async def generate_plan(self, intent, **kwargs):
            self.calls += 1
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled = True

    provider = SlowProvider()
    run = await RunService(
        run_repository, PlanningService(provider, validator, model_timeout_seconds=0.01)
    ).create(Intent("Plan"))
    assert run.error.code == ErrorCode.MODEL_TIMEOUT
    assert provider.calls == 1 and provider.cancelled and run_repository.get(run.run_id) == run


@pytest.mark.parametrize(
    "operation", ["create", "start_generation", "save_validated_plan", "route_validated_plan"]
)
async def test_storage_failures_never_replay_or_guess_success(
    run_repository, validator, provider_factory, monkeypatch, operation
):
    provider = provider_factory()

    def fail(*args):
        raise RunStoreError(RunErrorCode.DATABASE_UNAVAILABLE)

    monkeypatch.setattr(run_repository, operation, fail)
    with pytest.raises(RunStoreError) as caught:
        await RunService(run_repository, PlanningService(provider, validator)).create(
            Intent("Plan")
        )
    assert caught.value.code == RunErrorCode.DATABASE_UNAVAILABLE
    assert caught.value.run_id is not None
    assert provider.calls == (0 if operation in {"create", "start_generation"} else 1)
    states = {
        "create": None,
        "start_generation": "IntentReceived",
        "save_validated_plan": "PlanGenerating",
        "route_validated_plan": "PlanValidated",
    }
    rows = database_rows(run_repository.path)
    assert (rows["runs"][0][2] if rows["runs"] else None) == states[operation]


async def test_invalid_intent_and_insufficient_reserve_have_zero_calls(
    run_repository, validator, provider_factory
):
    provider = provider_factory()
    service = RunService(run_repository, PlanningService(provider, validator))
    with pytest.raises(ValidationFailure):
        await service.create(Intent(""))
    assert database_rows(run_repository.path)["runs"] == []
    run = await service.create(Intent("Plan"), deadline=asyncio.get_running_loop().time() + 1)
    assert run.version == 2 and run.error.code == ErrorCode.MODEL_TIMEOUT and provider.calls == 0
    assert [row[3] for row in database_rows(run_repository.path)["audit_events"]] == [
        "RUN_CREATED",
        "PLAN_REJECTED",
    ]


async def test_run_service_independently_validates_a_constructed_plan(run_repository, validator):
    invalid_plan = replace(plan_for(validator, Risk.SIDE_EFFECT), risk_class=Risk.READ_ONLY)

    class BypassedPlanningService:
        def __init__(self):
            self.validator = validator

        async def generate_plan(self, *args, **kwargs):
            return PlanningResult(PlanningState.PlanValidated, invalid_plan, None)

    run = await RunService(run_repository, BypassedPlanningService()).create(Intent("Plan"))
    assert run.plan is None and run.error.code == ErrorCode.PLAN_VALIDATION_ERROR


@pytest.mark.parametrize("barrier", ["create", "start_generation", "save_validated_plan"])
async def test_cancellation_at_commit_barriers(
    run_repository, validator, provider_factory, monkeypatch, barrier
):
    provider = provider_factory()
    original = getattr(run_repository, barrier)

    def cancelling(*args):
        run = original(*args)
        asyncio.current_task().cancel()
        return run

    monkeypatch.setattr(run_repository, barrier, cancelling)
    task = asyncio.create_task(
        RunService(run_repository, PlanningService(provider, validator)).create(Intent("Plan"))
    )
    with pytest.raises(asyncio.CancelledError):
        await task
    run = run_repository.get(database_rows(run_repository.path)["runs"][0][0])
    if barrier == "save_validated_plan":
        assert run.state == RunState.PlanValidated and run.version == 3 and run.plan is not None
        assert provider.calls == 1
    else:
        assert run.state == RunState.PlanRejected and run.error.code == ErrorCode.INTERNAL_ERROR
        assert run.version == (2 if barrier == "create" else 3) and provider.calls == 0


@pytest.mark.parametrize("reject_fails", [False, True])
async def test_cancellation_during_model_has_no_detached_generation(
    run_repository, validator, monkeypatch, reject_fails
):
    entered = asyncio.Event()
    stopped = asyncio.Event()

    class BlockingProvider:
        calls = 0

        async def generate_plan(self, intent, **kwargs):
            self.calls += 1
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

    attempts = []
    original = run_repository.reject_planning

    def reject(*args):
        attempts.append(args)
        if reject_fails:
            raise RunStoreError(RunErrorCode.DATABASE_UNAVAILABLE)
        return original(*args)

    monkeypatch.setattr(run_repository, "reject_planning", reject)
    provider = BlockingProvider()
    task = asyncio.create_task(
        RunService(run_repository, PlanningService(provider, validator)).create(Intent("Plan"))
    )
    async with asyncio.timeout(5):
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert stopped.is_set() and provider.calls == 1 and len(attempts) == 1
    run = run_repository.get(database_rows(run_repository.path)["runs"][0][0])
    assert run.state == (RunState.PlanGenerating if reject_fails else RunState.PlanRejected)


async def test_concurrent_requests_have_distinct_ids_and_isolated_plans(run_repository, validator):
    calls = Counter()
    entered = asyncio.Event()
    release = asyncio.Event()

    class IsolatedProvider:
        async def generate_plan(self, intent, **kwargs):
            calls[intent.text] += 1
            if sum(calls.values()) == 10:
                entered.set()
            await release.wait()
            plan = plan_for(validator).to_dict()
            plan["goal"] = intent.text
            return json.dumps(plan)

    service = RunService(run_repository, PlanningService(IsolatedProvider(), validator))
    tasks = [asyncio.create_task(service.create(Intent(f"Request {i}"))) for i in range(10)]
    async with asyncio.timeout(5):
        await entered.wait()
        release.set()
        runs = await asyncio.gather(*tasks)
    assert len({run.run_id for run in runs}) == 10
    assert calls == Counter({f"Request {i}": 1 for i in range(10)})
    for i, run in enumerate(runs):
        assert run.intent.text == run.plan.goal == f"Request {i}"
        assert run_repository.get(run.run_id) == run and run.version == 4


async def test_real_start_audit_transaction_failure_blocks_provider(
    run_repository, validator, provider_factory, monkeypatch
):
    import sqlite3

    append = run_repository._append_event

    def failing_append(connection, before, after, event_type, *args):
        if event_type == "PLAN_GENERATION_STARTED":
            raise sqlite3.OperationalError("Private SQL details")
        return append(connection, before, after, event_type, *args)

    monkeypatch.setattr(run_repository, "_append_event", failing_append)
    provider = provider_factory()
    with pytest.raises(RunStoreError) as caught:
        await RunService(run_repository, PlanningService(provider, validator)).create(
            Intent("Plan")
        )
    assert caught.value.code == RunErrorCode.DATABASE_UNAVAILABLE and provider.calls == 0
    persisted = run_repository.get(caught.value.run_id)
    assert persisted.state == RunState.IntentReceived and persisted.version == 1
    assert len(database_rows(run_repository.path)["audit_events"]) == 1
