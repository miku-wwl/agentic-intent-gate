"""Independent offline consumer. Import only the installed runtime's public integration API."""

import argparse
import asyncio
import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from uuid import UUID, uuid4

from agentic_intent_gate.runtime import (
    EXTERNAL_PAYLOAD_REVISION,
    ActionCommand,
    AdmissionCommand,
    ApprovalChoice,
    ApprovalDecisionRequest,
    BoundCapabilityAuthorization,
    CapabilityVerifier,
    ExecutableRegistry,
    Intent,
    Outcome,
    ToolBinding,
    ToolCapability,
    ToolOutcome,
    execution_budget_binding,
    open_runtime,
    provision_capability,
)

ARGUMENT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["entry"],
    "properties": {"entry": {"type": "string", "minLength": 1, "maxLength": 256}},
}


def binding_for(scope, execution_timeout_seconds=5):
    binding = ToolBinding(
        "append_audit_entry", "1", "consumer-audit", "audit-v1", "consumer-demo", scope
    )
    return execution_budget_binding(binding, execution_timeout_seconds)


class DeterministicProvider:
    """Test/example provider, deliberately not wired into either existing demo entrypoint."""

    def __init__(self, entry="Reviewed audit entry", raw=None):
        self.raw = (
            raw
            if raw is not None
            else json.dumps(
                {
                    "goal": "Append one reviewed audit entry",
                    "target": "consumer-demo",
                    "steps": [
                        {
                            "tool_name": "append_audit_entry",
                            "tool_version": "1",
                            "arguments": {"entry": entry},
                        }
                    ],
                    "risk_class": "SIDE_EFFECT",
                }
            )
        )

    async def generate_plan(self, intent, **kwargs):
        return self.raw


class AuditExecutor:
    """Consumer-owned effect store, separate from the gate; no control-plane credentials."""

    def __init__(self, path, binding, *, lose_ack=False):
        self.path, self.binding, self.lose_ack = Path(path), binding, lose_ack

    @staticmethod
    def provision(path, binding):
        # Explicit exclusive creation; no implicit provisioning/reset in execute().
        Path(path).touch(exist_ok=False)
        with closing(sqlite3.connect(path)) as db:
            db.executescript("""
                CREATE TABLE identity (singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                                       binding TEXT NOT NULL) STRICT;
                CREATE TABLE entries (effect_key TEXT PRIMARY KEY NOT NULL,
                    payload_digest TEXT NOT NULL, receipt TEXT UNIQUE NOT NULL,
                    entry TEXT NOT NULL) STRICT;
            """)
            db.execute(
                "INSERT INTO identity VALUES (1,?)", (json.dumps(vars(binding), sort_keys=True),)
            )
            db.commit()

    async def execute(self, spec, effect_key, context):
        def evidence(category, *, receipt=None, reason=None):
            return ToolOutcome(
                category,
                effect_key,
                spec.execution_scope,
                spec.target,
                spec.payload_digest(),
                receipt,
                reason,
            )

        args = dict(spec.arguments)
        if (
            not self.binding.matches(spec)
            or spec.identity_revision != EXTERNAL_PAYLOAD_REVISION
            or set(args) != {"entry"}
            or type(args["entry"]) is not str
            or not 1 <= len(args["entry"]) <= 256
            or len(args["entry"].encode("utf-8")) > 1024
        ):
            return evidence(Outcome.CONFIRMED_FAILURE, reason="INPUT_REJECTED")
        try:
            if (
                str(UUID(effect_key, version=4)) != effect_key
                or str(UUID(context.attempt_id, version=4)) != context.attempt_id
            ):
                return evidence(Outcome.CONFIRMED_FAILURE, reason="INPUT_REJECTED")
            if context.expired():
                return ToolOutcome(Outcome.UNKNOWN_OUTCOME)
            with closing(
                sqlite3.connect(self.path.resolve().as_uri() + "?mode=rw", uri=True, timeout=1)
            ) as db:
                db.execute("BEGIN IMMEDIATE")
                identity = db.execute("SELECT binding FROM identity WHERE singleton=1").fetchall()
                if identity != [(json.dumps(vars(self.binding), sort_keys=True),)]:
                    return evidence(Outcome.CONFIRMED_FAILURE, reason="STORE_IDENTITY_MISMATCH")
                row = db.execute(
                    "SELECT payload_digest,receipt,entry FROM entries WHERE effect_key=?",
                    (effect_key,),
                ).fetchone()
                payload = spec.payload_digest()
                if row:
                    if row[0] != payload:
                        return evidence(Outcome.CONFIRMED_FAILURE, reason="PAYLOAD_CONFLICT")
                    if row[2] != args["entry"]:
                        return ToolOutcome(Outcome.UNKNOWN_OUTCOME)
                    return evidence(Outcome.SUCCESS, receipt=row[1])
                receipt = str(uuid4())
                db.execute(
                    "INSERT INTO entries VALUES (?,?,?,?)",
                    (effect_key, payload, receipt, args["entry"]),
                )
                if context.expired():
                    return ToolOutcome(Outcome.UNKNOWN_OUTCOME)
                db.commit()
                if self.lose_ack:
                    raise sqlite3.OperationalError("Example effect COMMIT acknowledgement lost")
                if context.expired():
                    return ToolOutcome(Outcome.UNKNOWN_OUTCOME)
                return evidence(Outcome.SUCCESS, receipt=receipt)
        except Exception:
            return ToolOutcome(Outcome.UNKNOWN_OUTCOME)


def provision(directory, *, execution_timeout_seconds=5):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    if any(directory.iterdir()):
        raise ValueError("Provision only a new empty private consumer directory")
    binding = binding_for(str(uuid4()), execution_timeout_seconds)
    provision_capability(directory / "capability")
    # Configuration has no credentials; scope is generated once and retained across restart.
    (directory / "identity.json").write_text(json.dumps(vars(binding)), encoding="utf-8")
    AuditExecutor.provision(directory / "effects.sqlite3", binding)


@contextmanager
def consumer(
    directory,
    *,
    provider=None,
    lose_ack=False,
    predecessor_stopped=False,
    execution_timeout_seconds=5,
):
    directory = Path(directory).resolve()
    binding = ToolBinding(**json.loads((directory / "identity.json").read_text("utf-8")))
    capability = ToolCapability(binding, "Append one audit entry", ARGUMENT_SCHEMA)
    executor = AuditExecutor(directory / "effects.sqlite3", binding, lose_ack=lose_ack)
    verifier = CapabilityVerifier(
        (directory / "capability").read_text("ascii"),
        subject_id="consumer-operator",
        binding=binding,
        operations=("read", "admit", "authorize", "execute"),
    )
    policy = BoundCapabilityAuthorization(verifier, policy_revision="consumer-policy-v1")
    registry = ExecutableRegistry(executor, binding=binding)
    with open_runtime(
        gate_path=directory / "gate.sqlite3",
        provider=provider or DeterministicProvider(),
        capability=capability,
        registry=registry,
        verifier=verifier,
        policy=policy,
        predecessor_stopped=predecessor_stopped,
        execution_timeout_seconds=execution_timeout_seconds,
    ) as runtime:
        yield runtime, executor


async def workflow(runtime, header):
    # Represents explicit reviewed human consent for this deterministic example only.
    principal = runtime.verifier.verify(header)
    run = await runtime.run_service.create(Intent("Append a reviewed audit entry"))
    assert run.state.value == "AwaitingApproval"
    run = runtime.run_service.decide(run.run_id, ApprovalDecisionRequest(ApprovalChoice.APPROVE, 4))
    admission = AdmissionCommand(run.run_id, 5, principal.subject_id, str(uuid4()))
    action = runtime.gateway.admit(admission, principal).action
    action = runtime.gateway.authorize(
        ActionCommand(action.action_id, 1, str(uuid4())), principal
    ).action
    command = ActionCommand(action.action_id, 2, str(uuid4()))
    result = await runtime.gateway.execute(command, principal)
    replay = await runtime.gateway.execute(command, principal)
    assert replay.replayed and replay.action == result.action
    return result.action


def main():
    parser = argparse.ArgumentParser(description="Installed runtime: isolated offline consumer")
    parser.add_argument("directory", type=Path)
    parser.add_argument("--provision", action="store_true")
    parser.add_argument("--predecessor-stopped", action="store_true", required=True)
    parser.add_argument("--execution-timeout-seconds", type=float, default=5)
    args = parser.parse_args()
    if args.provision:
        provision(args.directory, execution_timeout_seconds=args.execution_timeout_seconds)
    with consumer(
        args.directory,
        predecessor_stopped=args.predecessor_stopped,
        execution_timeout_seconds=args.execution_timeout_seconds,
    ) as (runtime, _):
        header = "Bearer " + (args.directory / "capability").read_text("ascii")
        action = asyncio.run(workflow(runtime, header))
        assert action.state.value == "Succeeded"
        print(
            json.dumps(
                {
                    "state": action.state.value,
                    "action_id": action.action_id,
                    "receipt": action.result["receipt_reference"],
                }
            )
        )


if __name__ == "__main__":
    main()
