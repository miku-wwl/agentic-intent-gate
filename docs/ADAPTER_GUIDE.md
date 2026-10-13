# Adapter guide

Independent hosts can install the runtime and explicitly bind **one SIDE_EFFECT tool**
at startup. This is not plugin discovery, unrestricted routing, or multi-step execution.
Keep competition code and provider credentials in the consumer repository. Existing
demo entrypoints and the isolated Day 4 Dummy are not automatically extended.

## Supported consumer API

Import from `agentic_intent_gate.runtime`, not the package root. The main objects are
`ToolBinding`, `ToolCapability`, `ExecutableRegistry`, `CapabilityVerifier`,
`BoundCapabilityAuthorization`, `execution_budget_binding`, and `open_runtime`. Model/executor ports, Intent,
approval values, commands, and outcome types are available there too.

The complete executable [consumer](../examples/consumer/app.py) supplies every argument,
executor, provider, schema, private token and durable effect store. Run it from the checkout
with a fresh private directory (the UUID makes each invocation independent):

~~~powershell
# Omitted configuration: backward-compatible five seconds.
$defaultDemo = Join-Path $env:LOCALAPPDATA ('AgenticIntentGate\budget5-' + [guid]::NewGuid().ToString('N'))
uv run python examples/consumer/app.py $defaultDemo --provision --predecessor-stopped

# Explicit trusted fifteen-second opt-in, with a freshly provisioned scope/profile.
$extendedDemo = Join-Path $env:LOCALAPPDATA ('AgenticIntentGate\budget15-' + [guid]::NewGuid().ToString('N'))
uv run python examples/consumer/app.py $extendedDemo --provision --predecessor-stopped --execution-timeout-seconds 15
~~~

These are deterministic local effects and scripted example consent. For a real consumer,
the public composition uses the same `open_runtime` call as `consumer()` in that file.
Provision a new private directory/store separately;
retain its identity and token across restarts. The predecessor flag is an operator
attestation, not a process-stopping command. Serve inside the context so the existing
single-owner fence remains held. Existing v1 files are never silently migrated.

The following complete Python example runs from the checkout and reuses that consumer-owned
executor/provider. Its two new temporary ledgers demonstrate omission and explicit opt-in:

~~~python
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from examples.consumer.app import (
    ARGUMENT_SCHEMA, AuditExecutor, DeterministicProvider, provision, workflow,
)
from agentic_intent_gate.runtime import (
    BoundCapabilityAuthorization, CapabilityVerifier, ExecutableRegistry,
    ToolBinding, ToolCapability, open_runtime,
)

def host_configuration(directory, seconds):
    provision(directory, execution_timeout_seconds=seconds)
    binding = ToolBinding(**json.loads((directory / "identity.json").read_text("utf-8")))
    executor = AuditExecutor(directory / "effects.sqlite3", binding)
    verifier = CapabilityVerifier(
        (directory / "capability").read_text("ascii"), subject_id="consumer-operator",
        binding=binding, operations=("read", "admit", "authorize", "execute"),
    )
    return dict(
        gate_path=directory / "gate.sqlite3", provider=DeterministicProvider(),
        capability=ToolCapability(binding, "Append one audit entry", ARGUMENT_SCHEMA),
        registry=ExecutableRegistry(executor, binding=binding), verifier=verifier,
        policy=BoundCapabilityAuthorization(verifier, policy_revision="consumer-policy-v1"),
        predecessor_stopped=True,
    )

with TemporaryDirectory(prefix="aig-budget-example-") as root:
    default_directory = Path(root) / "default"
    with open_runtime(**host_configuration(default_directory, 5)) as runtime:
        token = "Bearer " + (default_directory / "capability").read_text("ascii")
        assert asyncio.run(workflow(runtime, token)).state.value == "Succeeded"

    extended_directory = Path(root) / "extended"
    with open_runtime(
        **host_configuration(extended_directory, 15), execution_timeout_seconds=15,
    ) as runtime:
        token = "Bearer " + (extended_directory / "capability").read_text("ascii")
        assert asyncio.run(workflow(runtime, token)).state.value == "Succeeded"
~~~

## Adapter execution budget

`open_runtime` adds keyword-only `execution_timeout_seconds=5`. Accepted values are finite
built-in int/float values in `(0, 15]`, including 5, 10 and 15. Invalid types/booleans raise
`TypeError`; non-positive, non-finite or oversized numbers raise `ValueError`. These are
host configuration failures, not adapter outcome evidence. Values above five require a
reviewed non-demo binding transformed with `execution_budget_binding(binding, value)`.
For 15, a base revision `audit-v2` becomes `audit-v2.exec-15s`; the complete revision must
fit the existing 64-character identifier limit. Use a new base revision when changing profiles.
The five-second default preserves existing demo/consumer identities; smaller budgets only
shorten that default authority.

Register the resulting binding consistently in capability, registry, verifier, policy and
consumer effect store. The extended compiler uses `action-compiler-v3`/`action-spec-v3`;
the budget suffix is durably hashed through the existing binding and grant identities.
Old compiler v1/v2 Actions cannot run under it, including coincidentally identical revision
names. Historical integrity/recovery uses retained metadata without migrating it.
Use a fresh scope and ledger for the new reviewed profile, fresh Run/Approval, and a separate
Authorization. A Plan approval predating admission does not itself record host configuration.

After durable claim, the Gateway establishes `loop.time() + configured_budget`, bounds it
by any finite `gateway.execute(..., deadline=absolute_monotonic_time)` caller deadline,
and passes the frozen deadline in `AttemptContext`. A caller cannot extend it. Invalid
deadlines raise `ActionError(INVALID_REQUEST)` before claim; already-expired deadlines
finish Unknown without adapter dispatch. The Gateway checks before dispatch and after
return, alongside `asyncio.timeout_at`; late success or no-effect evidence becomes Unknown.
Cancellation is propagated after durable Unknown finish. No detached retry/task is created.

Claim and finish are synchronous durable operations outside the adapter time allowance.
Budget enough request time for both. Provider dispatch, remote verification and local adapter
receipt persistence must fit **inside** the adapter budget. A blocking call cannot be preempted;
external commit may survive cancellation. Even an acknowledged effect becomes Unknown when
adapter receipt handling returns after deadline; lost acknowledgements are also Unknown.
An on-time confirmed receipt keeps its classification if only Gateway finish is delayed.
Finish ambiguity remains an error/interrupted state for recovery, never a guessed receipt.
These limits provide no universal remote exactly-once guarantee.

The bundled Action HTTP routes retain their existing ten-second caller request deadline,
which can shorten an extended budget. A consumer needing the full fifteen seconds supplies
its own request envelope with claim/finish reserve and calls the public Runtime Gateway;
changing a transport deadline never changes the approved Action's execution profile.

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
