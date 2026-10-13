"""Trusted budgets through the public Runtime and real Gateway/SQLite lifecycle.

Copy alongside test_consumer_runtime.py and the consumer example for wheel validation.
Synthetic effects use a separate real SQLite store; no external provider is contacted.
"""

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing, contextmanager
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from test_consumer_runtime import EXAMPLE, app, approved, authorized, header, rows

from agentic_intent_gate.application.action_compiler import ActionCompiler
from agentic_intent_gate.application.action_execution import ActionExecutionGateway
from agentic_intent_gate.domain.action import ActionCode, ActionError, ActionState
from agentic_intent_gate.domain.approval_binding import BindingCode, BindingError
from agentic_intent_gate.runtime import (
    ActionCommand,
    AdmissionCommand,
    BoundCapabilityAuthorization,
    CapabilityVerifier,
    ExecutableRegistry,
    Outcome,
    ToolCapability,
    ToolOutcome,
    execution_budget_binding,
    open_runtime,
)

INVALID_BUDGETS = [
    0,
    -1,
    15.0000000001,
    16,
    float("nan"),
    float("inf"),
    -float("inf"),
    True,
    False,
    None,
    "15",
    [],
    {},
    object(),
    Decimal("15"),
    10**1000,
]


@contextmanager
def configured(directory, budget=5, *, binding=None, executor=None, omit=False):
    binding = binding or app.ToolBinding(**json.loads((directory / "identity.json").read_text()))
    executor = executor or app.AuditExecutor(directory / "effects.sqlite3", binding)
    verifier = CapabilityVerifier(
        (directory / "capability").read_text("ascii"),
        subject_id="consumer-operator",
        binding=binding,
        operations=("read", "admit", "authorize", "execute"),
    )
    with open_runtime(
        gate_path=directory / "gate.sqlite3",
        provider=app.DeterministicProvider(),
        capability=ToolCapability(binding, "Audit", app.ARGUMENT_SCHEMA),
        registry=ExecutableRegistry(executor, binding=binding),
        verifier=verifier,
        policy=BoundCapabilityAuthorization(verifier, policy_revision="consumer-policy-v1"),
        predecessor_stopped=True,
        **({} if omit else {"execution_timeout_seconds": budget}),
    ) as runtime:
        yield SimpleNamespace(
            directory=directory,
            runtime=runtime,
            executor=executor,
            principal=verifier.verify(header(directory)),
        )


def provision(tmp_path, budget=5):
    directory = tmp_path / "consumer"
    app.provision(directory, execution_timeout_seconds=budget)
    return directory


def command(action):
    return ActionCommand(action.action_id, action.version, str(uuid4()))


def counts(env):
    with closing(sqlite3.connect(env.runtime.runs.path)) as db:
        return tuple(
            db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "action_attempts",
                "action_events",
                "action_command_receipts",
            )
        )


@pytest.mark.parametrize("budget,omit", [(5, True), (5, False), (10, False), (15, False)])
async def test_public_budget_forwarding_and_attempt_deadline(tmp_path, monkeypatch, budget, omit):
    directory = provision(tmp_path, budget)
    with configured(directory, budget, omit=omit) as env:
        gateway = env.runtime.gateway
        assert gateway.tool_timeout_seconds == budget
        assert env.runtime.actions.compiler.execution_timeout_seconds == budget
        with pytest.raises(FrozenInstanceError):
            gateway.tool_timeout_seconds = 16
        with pytest.raises(FrozenInstanceError):
            env.runtime.actions.compiler.execution_timeout_seconds = 16
        action = await authorized(env)
        assert action.spec.compiler_revision == (
            "action-compiler-v3" if budget > 5 else "action-compiler-v2"
        )
        original = env.executor.execute
        captured = []

        async def inspect(spec, key, context):
            remaining = context.deadline - asyncio.get_running_loop().time()
            assert budget - 0.1 <= remaining <= budget
            captured.append(context)
            with pytest.raises(FrozenInstanceError):
                context.deadline += 100
            return await original(spec, key, context)

        monkeypatch.setattr(env.executor, "execute", inspect)
        request = command(action)
        result = await gateway.execute(request, env.principal)
        assert result.action.state is ActionState.Succeeded
        assert len(rows(directory)) == 1 and counts(env)[0] == 1
        assert captured[0].cancelled.is_set()
        assert (await gateway.execute(request, env.principal)).replayed
        assert len(captured) == 1


@pytest.mark.parametrize("budget", INVALID_BUDGETS)
def test_public_invalid_configuration_before_gate_creation(tmp_path, budget):
    directory = provision(tmp_path)
    with pytest.raises((TypeError, ValueError)), configured(directory, budget):
        pytest.fail("Invalid configuration was accepted")
    assert not (directory / "gate.sqlite3").exists()
    assert not (directory / "gate.sqlite3.owner").exists()
    assert not rows(directory)


@pytest.mark.parametrize("budget", INVALID_BUDGETS)
def test_direct_gateway_invalid_configuration(tmp_path, budget):
    directory = provision(tmp_path)
    with configured(directory) as env, pytest.raises((TypeError, ValueError)):
        ActionExecutionGateway(
            env.runtime.actions,
            env.runtime.gateway.policy,
            env.runtime.gateway.registry,
            tool_timeout_seconds=budget,
        )


@pytest.mark.parametrize("budget", [0.01, 5, 10, 15])
def test_direct_gateway_valid_configuration(tmp_path, budget):
    directory = provision(tmp_path, budget)
    with configured(directory, budget) as env:
        gateway = ActionExecutionGateway(
            env.runtime.actions,
            env.runtime.gateway.policy,
            env.runtime.gateway.registry,
            tool_timeout_seconds=budget,
        )
        assert gateway.tool_timeout_seconds == budget


@pytest.mark.parametrize("budget", [10, 15])
def test_extended_budget_requires_explicit_binding_profile(tmp_path, budget):
    directory = provision(tmp_path)
    with pytest.raises(ValueError), configured(directory, budget):
        pytest.fail("Unreviewed legacy binding accepted")
    assert not (directory / "gate.sqlite3").exists()
    with configured(directory) as env, pytest.raises(ActionError):
        ActionExecutionGateway(
            env.runtime.actions,
            env.runtime.gateway.policy,
            env.runtime.gateway.registry,
            tool_timeout_seconds=budget,
        )


@pytest.mark.parametrize(
    "deadline",
    [
        float("nan"),
        float("inf"),
        -float("inf"),
        True,
        False,
        "soon",
        [],
        {},
        Decimal("1"),
        10**1000,
    ],
)
async def test_invalid_deadline_rejected_without_claim(tmp_path, deadline):
    directory = provision(tmp_path, 15)
    with configured(directory, 15) as env:
        action = await authorized(env)
        before = counts(env)
        with pytest.raises(ActionError) as error:
            await env.runtime.gateway.execute(command(action), env.principal, deadline=deadline)
        assert error.value.code is ActionCode.INVALID_REQUEST
        assert counts(env) == before
        assert env.runtime.actions.get(action.action_id).state is ActionState.Authorized
        assert not rows(directory)


@pytest.mark.parametrize("budget", [5, 10, 15])
@pytest.mark.parametrize("offset", [-1, 0.5, 1000])
async def test_caller_deadline_only_shortens_and_expired_never_dispatches(
    tmp_path,
    monkeypatch,
    budget,
    offset,
):
    directory = provision(tmp_path, budget)
    with configured(directory, budget) as env:
        action = await authorized(env)
        captured = []
        original = env.executor.execute

        async def inspect(spec, key, context):
            captured.append(context)
            return await original(spec, key, context)

        monkeypatch.setattr(env.executor, "execute", inspect)
        loop = asyncio.get_running_loop()
        started = loop.time()
        deadline = started + offset
        result = await env.runtime.gateway.execute(
            command(action), env.principal, deadline=deadline
        )
        if offset < 0:
            assert not captured and not rows(directory)
            assert result.action.state is ActionState.Unknown
        else:
            assert result.action.state is ActionState.Succeeded
            assert len(captured) == 1
            if offset < budget:
                assert captured[0].deadline == deadline
            else:
                assert started + budget <= captured[0].deadline < started + budget + 0.5


@pytest.mark.parametrize(
    "mode",
    [
        "timeout",
        "lost-ack",
        "error",
        "timeout-error",
        "failed",
        "late-success",
        "late-failure",
        "swallowed-timeout",
    ],
)
async def test_synthetic_effect_uncertainty_terminal_no_retry(tmp_path, monkeypatch, mode):
    directory = provision(tmp_path, 15)
    with configured(directory, 15) as env:
        action = await authorized(env)
        original = env.executor.execute
        calls, contexts = [], []
        loop = asyncio.get_running_loop()
        clock = [loop.time()]

        async def synthetic(spec, key, context):
            calls.append(key)
            contexts.append(context)
            if mode == "error":
                raise RuntimeError("Synthetic adapter fault")
            if mode == "timeout-error":
                raise TimeoutError("No no-effect evidence")
            if mode in {"failed", "late-failure"}:
                if mode == "late-failure":
                    clock[0] += 16
                return ToolOutcome(
                    Outcome.CONFIRMED_FAILURE,
                    key,
                    spec.execution_scope,
                    spec.target,
                    spec.payload_digest(),
                    no_effect_reason="INPUT_REJECTED",
                )
            outcome = await original(spec, key, context)
            assert outcome.category is Outcome.SUCCESS
            if mode == "lost-ack":
                raise RuntimeError("Committed synthetic remote effect, acknowledgement lost")
            if mode == "late-success":
                # Delayed synchronous receipt handling blocks asyncio's timeout callback.
                clock[0] += 16
            if mode in {"timeout", "swallowed-timeout"}:
                clock[0] += 16
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    if mode != "swallowed-timeout":
                        raise
            return outcome

        monkeypatch.setattr(env.executor, "execute", synthetic)
        request = command(action)
        with monkeypatch.context() as fake:
            fake.setattr(loop, "time", lambda: clock[0])
            result = await env.runtime.gateway.execute(request, env.principal)
        expected = ActionState.Failed if mode == "failed" else ActionState.Unknown
        assert result.action.state is expected and result.action.version == 4
        assert calls == [action.effect_key] and contexts[0].cancelled.is_set()
        effect_count = 0 if mode in {"error", "timeout-error", "failed", "late-failure"} else 1
        assert len(rows(directory)) == effect_count
        assert (await env.runtime.gateway.execute(request, env.principal)).replayed
        with pytest.raises(ActionError):
            await env.runtime.gateway.execute(command(result.action), env.principal)
        assert calls == [action.effect_key] and counts(env)[0] == 1
    with configured(directory, 15) as reopened:
        assert reopened.runtime.actions.get(action.action_id) == result.action
        assert reopened.runtime.actions.recover(owners_stopped=True) == 0
        with pytest.raises(ActionError):
            await reopened.runtime.gateway.execute(command(result.action), reopened.principal)
        assert len(rows(directory)) == effect_count


async def test_cancellation_after_effect_durable_unknown_and_propagated(tmp_path, monkeypatch):
    directory = provision(tmp_path, 15)
    with configured(directory, 15) as env:
        action = await authorized(env)
        entered, stopped = asyncio.Event(), asyncio.Event()
        original = env.executor.execute
        contexts = []

        async def waiting(spec, key, context):
            contexts.append(context)
            outcome = await original(spec, key, context)
            assert outcome.category is Outcome.SUCCESS
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        monkeypatch.setattr(env.executor, "execute", waiting)
        request = command(action)
        task = asyncio.create_task(env.runtime.gateway.execute(request, env.principal))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stopped.is_set() and contexts[0].cancelled.is_set()
        assert env.runtime.actions.get(action.action_id).state is ActionState.Unknown
        assert len(rows(directory)) == 1 and counts(env)[0] == 1
        assert (await env.runtime.gateway.execute(request, env.principal)).replayed


@pytest.mark.parametrize("stage", ["claim", "resolve", "adapter", "finish"])
async def test_deadline_crossing_synchronous_boundaries(tmp_path, monkeypatch, stage):
    directory = provision(tmp_path, 15)
    with configured(directory, 15) as env:
        action = await authorized(env)
        loop = asyncio.get_running_loop()
        real_time = loop.time
        clock = [real_time()]
        calls = []
        original = env.executor.execute

        async def effect(spec, key, context):
            calls.append(context)
            outcome = await original(spec, key, context)
            if stage == "adapter":
                clock[0] += 16
            return outcome

        monkeypatch.setattr(env.executor, "execute", effect)
        target = env.runtime.actions
        operation = "claim" if stage == "claim" else "finish"
        original_operation = getattr(target, operation)

        def delayed_operation(*args, **kwargs):
            result = original_operation(*args, **kwargs)
            clock[0] += 16
            return result

        if stage in {"claim", "finish"}:
            monkeypatch.setattr(target, operation, delayed_operation)
        if stage == "resolve":
            original_resolve = ExecutableRegistry.resolve

            def delayed_resolve(registry, spec):
                clock[0] += 16
                return original_resolve(registry, spec)

            monkeypatch.setattr(ExecutableRegistry, "resolve", delayed_resolve)
        with monkeypatch.context() as fake:
            fake.setattr(loop, "time", lambda: clock[0])
            result = await env.runtime.gateway.execute(command(action), env.principal)
        if stage in {"claim", "finish"}:
            # Trusted adapter budget begins AFTER durable claim; finish preserves valid evidence.
            assert result.action.state is ActionState.Succeeded and len(calls) == 1
        else:
            assert result.action.state is ActionState.Unknown
            assert len(calls) == (1 if stage == "adapter" else 0)


async def test_caller_deadline_crosses_durable_claim_no_dispatch(tmp_path, monkeypatch):
    directory = provision(tmp_path, 15)
    with configured(directory, 15) as env:
        action = await authorized(env)
        loop = asyncio.get_running_loop()
        now = loop.time()
        original = env.runtime.actions.claim

        def delayed_claim(*args):
            result = original(*args)
            patch.setattr(loop, "time", lambda: now + 2)
            return result

        with monkeypatch.context() as patch:
            patch.setattr(env.runtime.actions, "claim", delayed_claim)
            result = await env.runtime.gateway.execute(
                command(action), env.principal, deadline=now + 1
            )
        assert result.action.state is ActionState.Unknown and not rows(directory)


@pytest.mark.parametrize("same_revision", [False, True])
async def test_restart_extended_profile_cannot_elevate_old_authorization(
    tmp_path,
    same_revision,
):
    directory = provision(tmp_path)
    old_binding = app.ToolBinding(**json.loads((directory / "identity.json").read_text()))
    new_binding = execution_budget_binding(replace(old_binding, binding_revision="audit-v2"), 15)
    # Even a historical v0.1 binding whose name coincides with the new profile stays v2.
    binding = new_binding if same_revision else old_binding
    with configured(directory, binding=binding) as env:
        action = await authorized(env)
        assert action.spec.compiler_revision == "action-compiler-v2"
        retained = action.spec
    with configured(directory, 15, binding=new_binding) as extended:
        assert extended.runtime.actions.get(action.action_id).spec == retained
        before = counts(extended)
        with pytest.raises(ActionError) as error:
            await extended.runtime.gateway.execute(command(action), extended.principal)
        assert error.value.code is ActionCode.TOOL_NOT_BOUND
        with pytest.raises(ActionError):
            extended.runtime.gateway.authorize(command(action), extended.principal)
        with pytest.raises(ActionError):
            extended.runtime.gateway.admit(
                AdmissionCommand(
                    action.spec.run_id, 5, extended.principal.subject_id, str(uuid4())
                ),
                extended.principal,
            )
        assert counts(extended) == before and not rows(directory)
    # Switching back to the exact old default profile retains the original valid grant.
    with configured(directory, binding=binding) as restored:
        assert restored.runtime.actions.get(action.action_id).spec == retained
        assert restored.runtime.actions.get(action.action_id).state is ActionState.Authorized


@pytest.mark.parametrize("budget", [5, 10])
async def test_extended_action_rejects_other_budget_profiles(tmp_path, budget):
    directory = provision(tmp_path, 15)
    with configured(directory, 15) as env:
        action = await authorized(env)
        old = env.executor.binding
    binding = execution_budget_binding(replace(old, binding_revision="audit-v3"), budget)
    with configured(directory, budget, binding=binding) as changed:
        with pytest.raises(ActionError):
            await changed.runtime.gateway.execute(command(action), changed.principal)
        assert changed.runtime.actions.get(action.action_id).state is ActionState.Authorized
        assert not rows(directory)


@pytest.mark.parametrize(
    "field,value",
    [
        ("execution_scope", None),
        ("binding_revision", "audit-v2"),
        ("adapter_id", "another-adapter"),
    ],
)
async def test_extended_action_wrong_binding_fails_closed(tmp_path, field, value):
    directory = provision(tmp_path, 15)
    with configured(directory, 15) as env:
        action = await authorized(env)
        old = env.executor.binding
    new = replace(old, **{field: str(uuid4()) if value is None else value})
    if field == "binding_revision":
        new = execution_budget_binding(new, 15)
    with configured(directory, 15, binding=new) as changed:
        with pytest.raises(ActionError):
            await changed.runtime.gateway.execute(command(action), changed.principal)
        assert counts(changed)[0] == 0 and not rows(directory)


async def test_extended_profile_approval_and_authorization_still_required(tmp_path):
    directory = provision(tmp_path, 15)
    with configured(directory, 15) as env:
        run = await approved(env)
        action = env.runtime.gateway.admit(
            AdmissionCommand(run.run_id, 5, env.principal.subject_id, str(uuid4())),
            env.principal,
        ).action
        for principal in (None, replace(env.principal, _issuer=object()), env.principal):
            with pytest.raises(ActionError):
                await env.runtime.gateway.execute(command(action), principal)
        assert env.runtime.actions.get(action.action_id).state is ActionState.Pending
        assert counts(env)[0] == 0 and not rows(directory)


async def test_extended_profile_changed_approval_denies_before_claim(tmp_path):
    directory = provision(tmp_path, 15)
    with configured(directory, 15) as env:
        action = await authorized(env)
        with closing(sqlite3.connect(env.runtime.runs.path)) as db:
            db.execute("UPDATE approval_plan_bindings SET approval_plan_digest=?", ("0" * 64,))
            db.commit()
        with pytest.raises(BindingError) as error:
            await env.runtime.gateway.execute(command(action), env.principal)
        assert error.value.code is BindingCode.MISMATCH
        with closing(sqlite3.connect(env.runtime.runs.path)) as db:
            assert db.execute("SELECT COUNT(*) FROM action_attempts").fetchone()[0] == 0
        assert not rows(directory)


async def test_http_and_model_cannot_override_budget(tmp_path):
    directory = provision(tmp_path)
    with configured(directory) as env:
        action = await authorized(env)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=env.runtime.http_app()), base_url="http://127.0.0.1"
        ) as client:
            response = await client.post(
                f"/api/actions/{action.action_id}/execute",
                headers={"Authorization": header(directory), "Idempotency-Key": str(uuid4())},
                json={"expected_action_version": 2, "execution_timeout_seconds": 15},
            )
        assert response.status_code == 400
        assert env.runtime.gateway.tool_timeout_seconds == 5 and counts(env)[0] == 0
        raw = json.loads(app.DeterministicProvider().raw)
        raw["steps"][0]["arguments"]["execution_timeout_seconds"] = 15
        from agentic_intent_gate.domain.validation import ValidationFailure

        with pytest.raises(ValidationFailure):
            env.runtime.planning.validator.parse_plan(json.dumps(raw))
        assert not rows(directory)


def test_profile_identity_canonical_and_frozen():
    binding = app.binding_for(str(uuid4()))
    extended = execution_budget_binding(binding, 15)
    assert extended.binding_revision == "audit-v1.exec-15s"
    assert execution_budget_binding(binding, 15.0) == extended
    assert execution_budget_binding(binding, 5) == binding
    with pytest.raises(FrozenInstanceError):
        extended.binding_revision = "changed"
    with pytest.raises(ValueError):
        execution_budget_binding(extended, 10)
    with pytest.raises(ActionError):
        execution_budget_binding(replace(binding, binding_revision="x" * 64), 15)
    with pytest.raises(ValueError):
        ActionCompiler(None, (extended,), execution_timeout_seconds=10)


@pytest.mark.parametrize("budget", [5.000000000000001, 7.25, 10.0, 14.999999999999998, 15])
async def test_fractional_extended_profile_exact_binding(tmp_path, budget):
    directory = provision(tmp_path, budget)
    with configured(directory, budget) as env:
        action = await authorized(env)
        result = await env.runtime.gateway.execute(command(action), env.principal)
        assert result.action.state is ActionState.Succeeded
        assert len(rows(directory)) == 1
    with configured(directory, budget) as reopened:
        assert reopened.runtime.actions.get(action.action_id) == result.action


@pytest.mark.parametrize("mode", ["succeeded", "interrupted"])
def test_extended_actual_process_restart_never_replays_effect(tmp_path, mode):
    directory = provision(tmp_path, 15)
    code = """
import asyncio, runpy, sys
from pathlib import Path
from agentic_intent_gate.domain.action import ActionCode, ActionError
app = runpy.run_path(sys.argv[1])
directory = Path(sys.argv[2])
with app['consumer'](directory, execution_timeout_seconds=15,
                     predecessor_stopped=True) as (runtime, executor):
    if sys.argv[3] == 'interrupted':
        def no_finish(*args):
            raise ActionError(ActionCode.DATABASE_UNAVAILABLE)
        runtime.actions.finish = no_finish
    try:
        auth_header = 'Bearer ' + (directory/'capability').read_text('ascii')
        asyncio.run(app['workflow'](runtime, auth_header))
    except ActionError as error:
        assert sys.argv[3] == 'interrupted' and error.code is ActionCode.DATABASE_UNAVAILABLE
    actions = runtime.actions
    import sqlite3
    with sqlite3.connect(directory/'gate.sqlite3') as db:
        print(db.execute('SELECT action_id FROM actions').fetchone()[0])
"""
    child_env = dict(os.environ)
    child_env.pop("PYTHONPATH", None)
    process = subprocess.run(
        [sys.executable, "-c", code, str(EXAMPLE), str(directory), mode],
        cwd=tmp_path,
        env=child_env,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    action_id = process.stdout.strip()
    before = rows(directory)
    with configured(directory, 15) as reopened:
        action = reopened.runtime.actions.get(action_id)
        assert action.state is (
            ActionState.Succeeded if mode == "succeeded" else ActionState.Unknown
        )
        assert action.spec.compiler_revision == "action-compiler-v3"
        assert rows(directory) == before and len(before) == 1
        with pytest.raises(ActionError):
            asyncio.run(reopened.runtime.gateway.execute(command(action), reopened.principal))
        assert rows(directory) == before and counts(reopened)[0] == 1
