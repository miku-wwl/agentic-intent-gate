# Adapter guide

Independent hosts can install the runtime and explicitly bind **one SIDE_EFFECT tool**
at startup. This is not plugin discovery, unrestricted routing, or multi-step execution.
Keep competition code and provider credentials in the consumer repository. Existing
demo entrypoints and the isolated Day 4 Dummy are not automatically extended.

## Supported consumer API

Import from `agentic_intent_gate.runtime`, not the package root. The main objects are
`ToolBinding`, `ToolCapability`, `ExecutableRegistry`, `CapabilityVerifier`,
`BoundCapabilityAuthorization`, and `open_runtime`. Model/executor ports, Intent,
approval values, commands, and outcome types are available there too.

~~~python
from agentic_intent_gate.runtime import (
    ToolBinding, ToolCapability, ExecutableRegistry, CapabilityVerifier,
    BoundCapabilityAuthorization, open_runtime,
)

binding = ToolBinding(
    "append_audit_entry", "1", "consumer-audit", "audit-v1",
    "consumer-demo", retained_scope_uuid,
)
capability = ToolCapability(binding, "Append one audit entry", {
    "type": "object", "additionalProperties": False, "required": ["entry"],
    "properties": {"entry": {"type": "string", "minLength": 1, "maxLength": 256}},
})
registry = ExecutableRegistry(executor, binding=binding)
verifier = CapabilityVerifier(
    privately_loaded_token, subject_id="consumer-operator", binding=binding,
    operations=("read", "admit", "authorize", "execute"),
)
policy = BoundCapabilityAuthorization(verifier, policy_revision="consumer-policy-v1")
with open_runtime(
    gate_path=private_gate_path, provider=provider, capability=capability,
    registry=registry, verifier=verifier, policy=policy, predecessor_stopped=True,
) as runtime:
    # Use runtime.run_service / gateway, or serve runtime.http_app() on loopback here.
    ...
~~~

The named variables are host-owned, not implicitly discovered. See the runnable
[consumer](../examples/consumer/app.py). Provision a new private directory/store separately;
retain its identity and token across restarts. The predecessor flag is an operator
attestation, not a process-stopping command. Serve inside the context so the existing
single-owner fence remains held. Existing v1 files are never silently migrated.

## Consumer planning and admission boundary

The shared runtime accepts a trusted external `ModelProvider` supplied by the consumer.
The bundled `LocalOpenAIProvider` prompt is still tailored to the demo target
`service-demo` and the `arguments.service_id` convention; it is not a generic prompt for
non-demo capabilities. A consumer needing model-based planning for another domain must
provide its own appropriate trusted provider. Provider output remains subject to the
consumer's strict schema, semantic, and deterministic risk validation.

The shipped browser UI's Action admission control remains tailored to the demo
`append_service_note/1` action on `service-demo`; it is not a generic consumer UI. A host
can admit through the supported HTTP/API interface (including the existing Action
admission endpoint) or implement its own UI. Subsequent authorization and execution can
use the existing Runtime capabilities only within the explicitly registered single
SIDE_EFFECT binding, exact approved-Plan binding, and verified-capability policy. The
reference consumer uses deterministic planning and scripted reviewed approval. That
example is not production-grade multi-user authentication or proof of authenticated human
consent.

## Capability and Plan validation

Frozen bindings contain exact tool/version/target, adapter/revision, and canonical UUIDv4
scope. The registry captures the reviewed binding separately from the executor and has
no registration mutation API. Unregistered/mismatched identities are denied;
multiple/duplicate compiler bindings are rejected, not resolved by ordering.
A consumer executor cannot replace the legacy demo tool/target.

`ToolCapability` composes an in-memory Plan schema without editing bundled resources.
It accepts a closed flat object with 1–16 required fields, no additional fields,
references, defaults, nested objects, arrays, coercions, or implicit optional values.
Strings need explicit bounds within 1–1024 characters; integer bounds fit signed
64-bit values; booleans retain exact JSON types. Patterns are trusted host definitions.
Schema definitions and canonical arguments retain the 4 KiB byte bound.

Only the configured tool/version/target and one SIDE_EFFECT step are valid. Duplicate
keys, non-integer numbers, invalid Unicode, excessive bytes, unknown tools, extra
arguments, and model-supplied authority fields are rejected. Risk is host-defined.
`target_argument="target_id"` also requires the named argument to equal the pinned
Plan target. An optional `semantic_rule(arguments, target)` receives immutable arguments,
returns `None` on acceptance, and raises `ValidationFailure` on rejection; any non-None
return also rejects. Rules must be deterministic across restarts.

Run validation retains every lifecycle contract rule with the same trusted Plan resource.
Compilation rechecks the whole exact approval and approved version 5.
Approval is not authorization or execution.

## Authorization and executor contract

`BoundCapabilityAuthorization` checks verifier-issued subject, operation, scope,
complete binding, risk, and compiler revision. Its policy revision and binding are frozen.
One token does not grant authority over arbitrary tools. A reviewed consumer-specific
`AuthorizationPort` may instead be injected; preserve deny-by-default behavior.
Local capabilities are not production authentication or sandboxing of Python host code.

Implement the unchanged [ToolExecutor port](../src/agentic_intent_gate/ports/tool_executor.py):

~~~python
async def execute(self, spec: ExecutionSpec, effect_key: str,
                  context: AttemptContext) -> ToolOutcome:
    ...
~~~

Revalidate complete identity and exact bounded inputs. Configure provider destinations
and credentials in trusted code, never Plan URLs. Immutable arguments and all six binding
fields cross the port with an explicit payload revision. Context contains only attempt ID,
deadline, and cancellation event, not the gate database or approval token.
Bound provider operations; observe cancellation before and after dispatch.

## Identity, receipts, and ambiguity

External Actions persist `action-compiler-v2` and `action-spec-v2` in existing columns.
`ExecutionSpec.from_compiled` selects `tool-payload-v1`: canonical SHA-256 over arguments
and **all six authority fields**. The model cannot select revisions.
Legacy demo retains `action-compiler-v1`, `action-spec-v1`, and `demo-note-payload-v1`;
direct Dummy retains `dummy-record-v1` store identity. Existing Actions are not rehashed
or migrated. Keep prior trusted Plan definitions when upgrading a ledger;
changing tool/version/target is not an automatic data migration.

The core persists a UUIDv4 effect key once. Command idempotency keys are separate.
Atomically bind the effect key to its full payload and a durable unique receipt:

- Same key and payload: original receipt, no repeated effect.
- Same key, different payload: confirmed `PAYLOAD_CONFLICT`, never overwrite.
- Wrong store identity: confirmed `STORE_IDENTITY_MISMATCH`, no effect.
- Proven input rejection: confirmed `INPUT_REJECTED`, no effect.
- Timeout, exception, malformed evidence, uncertain commit/acceptance: Unknown.

Success requires correlated key, scope, target, payload digest, and UUID receipt.
Known failure requires a recognized no-effect reason and no receipt. Other evidence
fails closed. Only a confirmed durable claim permits one dispatch; the gate transaction
does not span asynchronous effects. Recovery changes interrupted Executing to Unknown
without executor calls. No automatic retry or reconciliation.

For remote APIs, establish native idempotency retention/conflict rules and durable
receipts. A local receipt store cannot make an uncooperative provider exactly-once.
Never infer no effect merely from HTTP 500.

## Reference paths and Hackathon workflow

The [consumer audit executor](../examples/consumer/app.py) owns separate real SQLite
effect/receipt storage through the public API and actual Action gateway. Its deterministic
provider and scripted approval are example/test behavior, not live LLM or authenticated
human approval. [Consumer tests](../tests/test_consumer_runtime.py) cover the complete
path, HTTP, identity denial, conflicts, concurrency, process restart, and lost commit
acknowledgements using real SQLite.

The unchanged [Day 4 Dummy](../src/agentic_intent_gate/infrastructure/tools/dummy_executor.py)
is a direct contract reference, not a public production tool.
Its injected test-owned SQLite store lives in
[contract tests](../tests/test_dummy_adapter_contract.py).
`dummy_record/1`, `test-local-dummy`, `day4-dummy`, `dummy-v1`, and UUIDv4 scope bind one
nonempty record (256 UTF-8 bytes; canonical arguments ≤1024 bytes). Its 105 contract and
eight direct integration tests are distinct from the consumer Action path.
Dummy is not registered into the demo or browser.

Put an adapter in your own repository; define one capability; test deterministic
identity/input validation; implement independent effect storage and narrow policy;
register explicitly; test denial/replay/conflicts/concurrency/restart/Unknown; demonstrate
Plan → reviewed approval → authorization → claim → effect → receipt.
Use focused tests before justified shared-runtime regression.

**NOT IMPLEMENTED:** multiple/unrestricted tools, multi-step or READ_ONLY dispatch,
discovery, MCP, multi-agent orchestration, remote-provider guarantees, or public Unknown
reconciliation. Installation alone cannot make an adapter executable.
See [Architecture](ARCHITECTURE.md), [Development](DEVELOPMENT.md), and [Security](SECURITY.md).
