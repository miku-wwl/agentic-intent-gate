"""Consumer-owned adapter through the public API and real independent SQLite files.

This file can also be copied with examples/consumer/app.py outside the checkout and
run against an installed wheel. It never adds core src/ to sys.path or PYTHONPATH.
"""

import asyncio
import json
import os
import runpy
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

import agentic_intent_gate.infrastructure.persistence.sqlite_actions as action_storage
from agentic_intent_gate.application.action_compiler import ActionCompiler
from agentic_intent_gate.domain.action import ActionCode, ActionError, ActionState
from agentic_intent_gate.domain.approval_binding import BindingError, digest
from agentic_intent_gate.domain.contracts import thaw_json
from agentic_intent_gate.domain.run import RunState, RunStoreError
from agentic_intent_gate.domain.validation import ValidationFailure
from agentic_intent_gate.runtime import (
    EXTERNAL_PAYLOAD_REVISION,
    ActionCommand,
    AdmissionCommand,
    ApprovalChoice,
    ApprovalDecisionRequest,
    AttemptContext,
    BoundCapabilityAuthorization,
    CapabilityVerifier,
    ExecutableRegistry,
    ExecutionSpec,
    Intent,
    Outcome,
    ToolCapability,
)

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "consumer" / "app.py"
app = SimpleNamespace(**runpy.run_path(str(EXAMPLE)))


def header(directory):
    return "Bearer " + (directory / "capability").read_text("ascii")


def rows(directory):
    with closing(sqlite3.connect(directory / "effects.sqlite3")) as db:
        return db.execute("SELECT * FROM entries ORDER BY effect_key").fetchall()


@pytest.fixture
def env(tmp_path):
    directory = tmp_path / "consumer"
    app.provision(directory)
    with app.consumer(directory, predecessor_stopped=True) as (runtime, executor):
        yield SimpleNamespace(
            directory=directory,
            runtime=runtime,
            executor=executor,
            principal=runtime.verifier.verify(header(directory)),
        )


async def approved(env):
    runtime = env.runtime
    run = await runtime.run_service.create(Intent("Append a reviewed audit entry"))
    assert run.state is RunState.AwaitingApproval and run.version == 4
    return runtime.run_service.decide(
        run.run_id, ApprovalDecisionRequest(ApprovalChoice.APPROVE, 4)
    )


async def authorized(env):
    run = await approved(env)
    command = AdmissionCommand(run.run_id, 5, env.principal.subject_id, str(uuid4()))
    action = env.runtime.gateway.admit(command, env.principal).action
    replay = env.runtime.gateway.admit(command, env.principal)
    assert replay.replayed and replay.action == action
    command = ActionCommand(action.action_id, 1, str(uuid4()))
    result = env.runtime.gateway.authorize(command, env.principal)
    assert env.runtime.gateway.authorize(command, env.principal).replayed
    return result.action


async def test_complete_public_consumer_flow_and_receipt(env):
    action = await app.workflow(env.runtime, header(env.directory))
    assert action.state is ActionState.Succeeded and action.version == 4
    assert action.spec.compiler_revision == "action-compiler-v2"
    assert action.result["message"] == "The tool effect was confirmed."
    assert len(rows(env.directory)) == 1
    assert rows(env.directory)[0][0] == action.effect_key
    assert rows(env.directory)[0][2] == action.result["receipt_reference"]
    assert env.runtime.actions.get(action.action_id) == action
    with closing(sqlite3.connect(env.runtime.runs.path)) as db:
        assert db.execute("SELECT COUNT(*) FROM action_attempts").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM action_events").fetchone()[0] == 4
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2


async def test_existing_http_contract_with_consumer_executor(env):
    transport = httpx.ASGITransport(app=env.runtime.http_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        response = await client.post("/api/runs", json={"text": "Append an audit entry"})
        assert response.status_code == 201
        run = response.json()
        response = await client.post(
            f"/api/runs/{run['run_id']}/approval",
            json={"decision": "APPROVE", "expected_version": 4},
        )
        assert response.status_code == 200
        path = f"/api/runs/{run['run_id']}/actions"
        assert (await client.post(path, json={"expected_run_version": 5})).status_code == 401
        auth = {"Authorization": header(env.directory), "Idempotency-Key": str(uuid4())}
        response = await client.post(path, headers=auth, json={"expected_run_version": 5})
        assert response.status_code == 201
        action = response.json()["actions"][0]
        for operation, version in (("authorize", 1), ("execute", 2)):
            auth["Idempotency-Key"] = str(uuid4())
            response = await client.post(
                f"/api/actions/{action['action_id']}/{operation}",
                headers=auth,
                json={"expected_action_version": version},
            )
            assert response.status_code == 200
        assert response.json()["action"]["state"] == "Succeeded" and len(rows(env.directory)) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("tool_name", "other_tool"),
        ("tool_version", "2"),
        ("target", "other-target"),
        ("adapter_id", "other-adapter"),
        ("binding_revision", "audit-v2"),
        ("execution_scope", "other-scope"),
        ("identity_revision", "demo-note-payload-v1"),
    ],
)
async def test_registry_and_policy_deny_identity_substitution(env, field, value):
    action = await authorized(env)
    if field == "execution_scope":
        value = str(uuid4())
    spec = ExecutionSpec.from_compiled(action.spec)
    with pytest.raises(ActionError, match="binding"):
        ExecutableRegistry(env.executor, binding=env.executor.binding).resolve(
            replace(spec, **{field: value})
        )
    if field != "identity_revision":
        with pytest.raises(ActionError) as error:
            env.runtime.gateway.policy.permit(
                env.principal, "execute", replace(action.spec, **{field: value})
            )
        assert error.value.code is ActionCode.NOT_AUTHORIZED
    assert not rows(env.directory)


async def test_no_registration_or_duplicate_registration(env):
    action = await authorized(env)
    spec = ExecutionSpec.from_compiled(action.spec)
    with pytest.raises(ActionError):
        ExecutableRegistry().resolve(spec)
    with pytest.raises(ActionError):
        ExecutableRegistry(env.executor)
    with pytest.raises(ActionError):
        ActionCompiler(env.runtime.planning.validator, (env.executor.binding, env.executor.binding))
    registry = ExecutableRegistry(env.executor, binding=env.executor.binding)
    with pytest.raises(FrozenInstanceError):
        registry.bindings = ()
    with pytest.raises(FrozenInstanceError):
        registry._executor = object()
    assert not rows(env.directory)


@pytest.mark.parametrize(
    "kind", ["anonymous", "forged", "wrong-operation", "approval-only", "pending"]
)
async def test_approval_does_not_grant_execution(env, kind):
    run = await approved(env)
    if kind == "approval-only":
        assert env.runtime.gateway.list_for_run(run.run_id, env.principal) == ()
        with pytest.raises(ActionError):
            await env.runtime.gateway.execute(
                ActionCommand(str(uuid4()), 2, str(uuid4())), env.principal
            )
    else:
        action = env.runtime.gateway.admit(
            AdmissionCommand(run.run_id, 5, env.principal.subject_id, str(uuid4())), env.principal
        ).action
        principal = env.principal
        if kind == "anonymous":
            principal = None
        elif kind == "forged":
            principal = replace(principal, _issuer=object())
        elif kind == "wrong-operation":
            principal = replace(principal, operations=frozenset({"read"}))
        with pytest.raises(ActionError):
            await env.runtime.gateway.execute(
                ActionCommand(action.action_id, 1, str(uuid4())), principal
            )
        assert env.runtime.actions.get(action.action_id).state is ActionState.Pending
    assert not rows(env.directory)


@pytest.mark.parametrize(
    "kind",
    [
        "tool",
        "version",
        "target",
        "extra-argument",
        "missing-argument",
        "float",
        "bool",
        "oversize",
        "risk",
        "adapter",
        "scope",
        "multistep",
        "duplicate-key",
        "surrogate",
        "huge-int",
    ],
)
async def test_untrusted_plan_rejected_before_approval_or_dispatch(env, kind):
    value = json.loads(app.DeterministicProvider().raw)
    step = value["steps"][0]
    if kind in {"tool", "version"}:
        step["tool_name" if kind == "tool" else "tool_version"] = "untrusted"
    elif kind == "target":
        value["target"] = "untrusted"
    elif kind == "extra-argument":
        step["arguments"]["url"] = "https://untrusted.invalid"
    elif kind == "missing-argument":
        step["arguments"] = {}
    elif kind in {"float", "bool", "oversize", "surrogate", "huge-int"}:
        step["arguments"]["entry"] = {
            "float": 1.5,
            "bool": True,
            "oversize": "x" * 257,
            "surrogate": "\ud800",
            "huge-int": 2**63,
        }[kind]
    elif kind == "risk":
        value["risk_class"] = "READ_ONLY"
    elif kind in {"adapter", "scope"}:
        step[kind] = "untrusted"
    elif kind == "multistep":
        value["steps"] *= 2
    raw = json.dumps(value)
    if kind == "duplicate-key":
        raw = raw.replace('"entry":', '"entry":"first","entry":')
    with pytest.raises(ValidationFailure):
        env.runtime.planning.validator.parse_plan(raw)
    env.runtime.planning.provider = app.DeterministicProvider(raw=raw)
    run = await env.runtime.run_service.create(Intent("Untrusted proposal"))
    assert run.state is RunState.PlanRejected
    assert env.runtime.gateway.list_for_run(run.run_id, env.principal) == ()
    assert not rows(env.directory)


async def test_stale_or_changed_approved_plan_denied(env):
    run = await approved(env)
    with pytest.raises(RunStoreError):
        env.runtime.run_service.decide(
            run.run_id, ApprovalDecisionRequest(ApprovalChoice.APPROVE, 4)
        )
    changed = run.plan.to_dict()
    changed["goal"] = "Different reviewed goal"
    with closing(sqlite3.connect(env.runtime.runs.path)) as db:
        db.execute(
            "UPDATE runs SET validated_plan_json=? WHERE run_id=?",
            (
                json.dumps(changed, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                run.run_id,
            ),
        )
        db.commit()
    with pytest.raises((ActionError, BindingError, RunStoreError)):
        env.runtime.gateway.admit(
            AdmissionCommand(run.run_id, 5, env.principal.subject_id, str(uuid4())), env.principal
        )
    assert not rows(env.directory)


async def test_payload_conflict_and_same_key_replay(env):
    action = await authorized(env)
    spec = ExecutionSpec.from_compiled(action.spec)
    context = AttemptContext(str(uuid4()), asyncio.get_running_loop().time() + 5, asyncio.Event())
    first = await env.executor.execute(spec, action.effect_key, context)
    replay = await env.executor.execute(spec, action.effect_key, context)
    assert first.category is Outcome.SUCCESS and replay == first
    conflict = await env.executor.execute(
        replace(spec, arguments={"entry": "different"}), action.effect_key, context
    )
    assert (
        conflict.category is Outcome.CONFIRMED_FAILURE
        and conflict.no_effect_reason == "PAYLOAD_CONFLICT"
    )
    assert len(rows(env.directory)) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("tool_name", "other"),
        ("tool_version", "2"),
        ("target", "other"),
        ("adapter_id", "other"),
        ("binding_revision", "audit-v2"),
        ("execution_scope", str(uuid4())),
    ],
)
def test_external_digest_includes_every_authority_field(field, value):
    spec = ExecutionSpec(
        "append_audit_entry",
        "1",
        "consumer-demo",
        {"entry": "one"},
        "consumer-audit",
        "audit-v1",
        str(uuid4()),
        EXTERNAL_PAYLOAD_REVISION,
    )
    assert spec.payload_digest() != replace(spec, **{field: value}).payload_digest()
    expected = {
        "identity_revision": EXTERNAL_PAYLOAD_REVISION,
        **vars(spec),
        "arguments": dict(spec.arguments),
    }
    assert spec.payload_digest() == digest(expected)
    with pytest.raises(TypeError):
        spec.arguments["entry"] = "changed"


def test_legacy_digest_unchanged():
    spec = ExecutionSpec(
        "append_service_note",
        "1",
        "service-demo",
        {"service_id": "service-demo", "note": "one"},
        "demo-note-adapter",
        "demo-binding-v1",
        str(uuid4()),
    )
    assert spec.payload_digest() == digest(
        {
            "identity_revision": "demo-note-payload-v1",
            "tool_name": spec.tool_name,
            "tool_version": spec.tool_version,
            "target": spec.target,
            "execution_scope": spec.execution_scope,
            "arguments": dict(spec.arguments),
        }
    )


@pytest.mark.parametrize("same_key", [True, False])
async def test_concurrent_claims_preserve_single_effect(env, same_key):
    action = await authorized(env)
    barrier, key = Barrier(8), str(uuid4())

    def attempt(_):
        barrier.wait(timeout=5)
        try:
            return asyncio.run(
                env.runtime.gateway.execute(
                    ActionCommand(action.action_id, 2, key if same_key else str(uuid4())),
                    env.principal,
                )
            )
        except ActionError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(8)))
    assert sum(r is not None and not r.replayed for r in results) == 1
    assert len(rows(env.directory)) == 1
    assert env.runtime.actions.get(action.action_id).state is ActionState.Succeeded
    with closing(sqlite3.connect(env.runtime.runs.path)) as db:
        assert db.execute("SELECT COUNT(*) FROM action_attempts").fetchone()[0] == 1


async def test_lost_effect_commit_ack_unknown_and_never_redispatched(env):
    action = await authorized(env)
    env.executor.lose_ack = True
    command = ActionCommand(action.action_id, 2, str(uuid4()))
    result = await env.runtime.gateway.execute(command, env.principal)
    assert result.action.state is ActionState.Unknown and len(rows(env.directory)) == 1
    before = rows(env.directory)
    assert (await env.runtime.gateway.execute(command, env.principal)).replayed
    with pytest.raises(ActionError):
        await env.runtime.gateway.execute(
            ActionCommand(action.action_id, 4, str(uuid4())), env.principal
        )
    assert rows(env.directory) == before


async def test_unconfirmed_real_claim_commit_never_dispatches(env, monkeypatch):
    action = await authorized(env)
    command = ActionCommand(action.action_id, 2, str(uuid4()))

    class LostAck(sqlite3.Connection):
        def commit(self):
            super().commit()
            raise sqlite3.OperationalError("Real claim COMMIT acknowledged ambiguously")

    def open_lost(path):
        db = sqlite3.connect(path, isolation_level=None, factory=LostAck)
        db.row_factory = sqlite3.Row
        return db

    with monkeypatch.context() as fault:
        fault.setattr(action_storage, "open_existing", open_lost)
        with pytest.raises(ActionError) as error:
            await env.runtime.gateway.execute(command, env.principal)
        assert error.value.code is ActionCode.DATABASE_UNAVAILABLE
    assert env.runtime.actions.get(action.action_id).state is ActionState.Executing
    assert not rows(env.directory)
    assert (await env.runtime.gateway.execute(command, env.principal)).replayed
    assert not rows(env.directory)
    assert env.runtime.actions.recover(owners_stopped=True) == 1
    assert env.runtime.actions.get(action.action_id).state is ActionState.Unknown
    assert not rows(env.directory)


@pytest.mark.parametrize("mode", ["succeeded", "interrupted"])
def test_actual_process_restart_durable_receipts_and_unknown(tmp_path, mode):
    directory = tmp_path / "consumer"
    app.provision(directory)
    # Separate Python processes: no inherited in-memory runtime, policies, or connections.
    code = """
import asyncio, json, runpy, sys
from pathlib import Path
from uuid import uuid4
from agentic_intent_gate.runtime import *
app=runpy.run_path(sys.argv[1]); directory=Path(sys.argv[2]); mode=sys.argv[3]
with app['consumer'](directory,predecessor_stopped=True) as (runtime,executor):
    principal=runtime.verifier.verify('Bearer '+(directory/'capability').read_text('ascii'))
    if mode=='succeeded':
        action=asyncio.run(app['workflow'](
            runtime,'Bearer '+(directory/'capability').read_text('ascii')))
    else:
        run=asyncio.run(runtime.run_service.create(Intent('Append entry')))
        run=runtime.run_service.decide(run.run_id,ApprovalDecisionRequest(ApprovalChoice.APPROVE,4))
        action=runtime.gateway.admit(AdmissionCommand(run.run_id,5,principal.subject_id,str(uuid4())),principal).action
        action=runtime.gateway.authorize(ActionCommand(action.action_id,1,str(uuid4())),principal).action
        claim=runtime.actions.claim(ActionCommand(action.action_id,2,str(uuid4())),principal,runtime.gateway.policy)
        spec=ExecutionSpec.from_compiled(claim.action.spec)
        async def apply():
            context=AttemptContext(claim.attempt_id,
                asyncio.get_running_loop().time()+5,asyncio.Event())
            return await executor.execute(spec,claim.action.effect_key,context)
        assert asyncio.run(apply()).category is Outcome.SUCCESS
        action=claim.action
    print(json.dumps({'id':action.action_id,'key':action.effect_key,'state':action.state.value}))
"""
    child_env = dict(os.environ)
    child_env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, "-c", code, str(EXAMPLE), str(directory), mode],
        cwd=tmp_path,
        env=child_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=True,
    )
    first = json.loads(result.stdout)
    assert first["state"] == ("Succeeded" if mode == "succeeded" else "Executing")
    before = rows(directory)
    with app.consumer(directory, predecessor_stopped=True) as (runtime, _):
        action = runtime.actions.get(first["id"])
        assert action.state.value == ("Succeeded" if mode == "succeeded" else "Unknown")
        assert action.effect_key == first["key"] and rows(directory) == before and len(before) == 1
        principal = runtime.verifier.verify(header(directory))
        with pytest.raises(ActionError):
            asyncio.run(
                runtime.gateway.execute(ActionCommand(action.action_id, 4, str(uuid4())), principal)
            )


@pytest.mark.parametrize(
    "kind", ["additional", "unbounded", "remote-ref", "nested", "optional", "default"]
)
def test_unsafe_schema_configuration_denied(kind):
    schema = thaw_json(app.ARGUMENT_SCHEMA)
    if kind == "additional":
        schema["additionalProperties"] = True
    elif kind == "unbounded":
        del schema["properties"]["entry"]["maxLength"]
    elif kind == "remote-ref":
        schema["properties"]["entry"] = {"$ref": "https://untrusted.invalid"}
    elif kind == "nested":
        schema["properties"]["entry"] = {"type": "object"}
    elif kind == "optional":
        schema["required"] = []
    else:
        schema["properties"]["entry"]["default"] = "implicit"
    with pytest.raises(ValueError):
        ToolCapability(app.binding_for(str(uuid4())), "Audit", schema)


def test_bound_policy_revision_frozen_and_target_semantics():
    binding = app.binding_for(str(uuid4()))
    verifier = CapabilityVerifier(
        "a" * 64, subject_id="operator", binding=binding, operations=("read",)
    )
    policy = BoundCapabilityAuthorization(verifier, policy_revision="policy-v1")
    with pytest.raises(FrozenInstanceError):
        policy.policy_revision = "policy-v2"
    schema = thaw_json(app.ARGUMENT_SCHEMA)
    schema["properties"]["target_id"] = {"type": "string", "minLength": 1, "maxLength": 64}
    schema["required"].append("target_id")
    capability = ToolCapability(binding, "Audit", schema, target_argument="target_id")
    schema["properties"]["entry"]["maxLength"] = 1000
    assert capability.input_schema["properties"]["entry"]["maxLength"] == 256
    value = json.loads(app.DeterministicProvider().raw)
    value["steps"][0]["arguments"]["target_id"] = "wrong-target"
    with pytest.raises(ValidationFailure):
        capability.build_validator().parse_plan(json.dumps(value))


@pytest.mark.parametrize(
    "field,value",
    [
        ("tool_name", "untrusted"),
        ("tool_version", "2"),
        ("target", "other"),
        ("adapter_id", "other"),
        ("binding_revision", "audit-v2"),
        ("execution_scope", "new-scope"),
        ("compiler_revision", "action-compiler-v1"),
        ("spec_digest", "0" * 64),
    ],
)
async def test_persisted_external_spec_tamper_is_storage_failure_before_effect(env, field, value):
    action = await authorized(env)
    if field == "execution_scope":
        value = str(uuid4())
    with closing(sqlite3.connect(env.runtime.runs.path)) as db:
        db.execute(f"UPDATE actions SET {field}=? WHERE action_id=?", (value, action.action_id))
        db.commit()
    with pytest.raises(ActionError) as error:
        await env.runtime.gateway.execute(
            ActionCommand(action.action_id, 2, str(uuid4())), env.principal
        )
    assert error.value.code is ActionCode.DATABASE_UNAVAILABLE
    assert not rows(env.directory)


@pytest.mark.parametrize(
    "field",
    ["tool_name", "tool_version", "target", "adapter_id", "binding_revision", "execution_scope"],
)
def test_binding_rejects_invalid_identifiers(field):
    with pytest.raises(ActionError):
        replace(app.binding_for(str(uuid4())), **{field: "untrusted\n"})


def test_trusted_semantics_cannot_silently_mutate_or_accept_false():
    binding = app.binding_for(str(uuid4()))
    raw = app.DeterministicProvider().raw

    def rejected(arguments, target):
        return False

    capability = ToolCapability(binding, "Audit", app.ARGUMENT_SCHEMA, semantic_rule=rejected)
    with pytest.raises(ValidationFailure):
        capability.build_validator().parse_plan(raw)

    def mutation(arguments, target):
        arguments["entry"] = "unreviewed"

    capability = ToolCapability(binding, "Audit", app.ARGUMENT_SCHEMA, semantic_rule=mutation)
    with pytest.raises(TypeError):
        capability.build_validator().parse_plan(raw)
