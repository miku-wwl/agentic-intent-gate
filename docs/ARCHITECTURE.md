# Architecture

The system is a local approval-gated runtime with two unchanged demo entry points, an
explicit independent-consumer composition, and an isolated adapter-test lane.
Source lives under `src/agentic_intent_gate/`.

## Source layers

| Directory | Responsibility |
| --- | --- |
| `api/` | HTTP routes, transport parsing, and `static/index.html` serving |
| `application/` | Planning, Run/approval, Action compilation/execution, and recovery use cases |
| `domain/` | Immutable contracts, state graphs, exact binding, and validation invariants |
| `ports/` | Repository, model, authorization, and executor Protocols/contracts |
| `infrastructure/persistence/` | SQLite repositories, ledger, schemas, and explicit migration |
| `infrastructure/llm/` | Concrete local OpenAI-compatible provider |
| `infrastructure/security/` | Local capability verification/policy and single-owner lock |
| `infrastructure/tools/` | Demo executor, isolated Dummy, proposal catalog, and concrete registry |
| `config/` | Environment loading and loopback/model/database settings validation |

`main.py` and `execution_main.py` remain composition/CLI entry points. Only the top-level
package has `__init__.py`; internal directories use PEP 420 namespaces. No plugin discovery,
new API, database migration, or security behavior is introduced by this source layout.

~~~mermaid
flowchart TD
    I[Natural-language Intent] --> P[Local LLM / PlanningService]
    P --> V[Schema + semantic validation]
    V --> G[Deterministic risk Gate]
    G --> R[Persistent Run]
    R -->|Read-only Plan| RO[Ready: no execution]
    R -->|Write Plan| H[Human approval / rejection]
    H -->|Approved only; v2 composition| B[Exact approved-Plan binding: v2]
    B --> C[Trusted ActionCompiler]
    C --> A[Verified capability + authorization]
    A --> L[SQLite Action ledger / CAS claim]
    L --> E[Exact registered ToolExecutor]
    E --> S[Host-owned effect / receipt store]
    E --> O[Durable Succeeded / Failed / Unknown]
    subgraph Isolated_tests[Day 4 isolated tests: no production registration]
        T[Direct contract / integration calls] --> D[DeterministicDummyExecutor]
        D --> Q[Test-owned SQLite receipt store]
    end
~~~

## Intent, Plan, and deterministic Gate

`PlanningService` passes natural-language Intent to `LocalOpenAIProvider`, which requests
one non-streaming JSON chat completion. Provider/envelope limits, duplicate-key rejection,
JSON Schema, and semantic validation reject malformed or unsupported Plans; there is no
automatic repair or fallback. Validation does not prove fidelity to the user's intent.

The catalog describes `get_service_record/1` (read-only), `append_service_note/1`
(side-effect), and `set_maintenance_mode/1` (approval-required), all for `service-demo`.
Semantic checks enforce tool/argument consistency, target identity, ordering, and risk
derived from trusted specifications rather than a model's risk claim. The maintenance
revision is a demo expectation, not a live infrastructure lookup.
Catalog membership is not execution authorization.

## Runs and human approval

`RunService` and the repository persist:

- `IntentReceived → PlanGenerating → PlanValidated`.
- Validated read-only Plans → `Ready`; write Plans → `AwaitingApproval`.
- `AwaitingApproval → Approved` or `Rejected`.
- Planning rejection/interrupted planning → `PlanRejected`.

Terminal Run states do not authorize new planning or implicit tool dispatch. Mutations
use expected versions, and Run/approval/audit writes are transactional. A decision
consumes the expected awaiting-approval version once.

SQLite v2 approval binds the canonical **whole Plan** and its identity to the decision,
Run version, and persisted decision metadata. Admission verifies that exact binding;
legacy unbound decisions are not executable. A digest is an integrity identity, not proof
of an authenticated human. Approval and Action authorization are distinct operations.

## Two application entry points

**Default `aig-plans`:** `main.py` composes the local provider, validator,
`SQLiteRunRepository` (v1), `PlanningService`, `RunService`, startup Run recovery, and HTTP/UI.
It does not compose an Action gateway or expose Action controls.

**Explicit v2 demo:** `execution_main.py` requires private execution configuration and
`--predecessor-stopped`. It holds a single-owner lock, composes `SQLiteV2RunRepository`,
`ActionCompiler`, `SQLiteExecutionRepository`, `CapabilityVerifier`,
`LocalDemoAuthorization`, `ExecutableRegistry`, and `DemoNoteExecutor`, then recovers
Runs/Actions before serving. New empty gate files can be initialized; existing v1 files
are not silently migrated. The demo store is provisioned separately.

The unchanged demo still registers only `DemoNoteExecutor`. Independent hosts use
`runtime.ToolCapability` and `open_runtime` to compose one non-demo tool: closed bounded
scalar arguments, one pinned target, SIDE_EFFECT risk, optional trusted semantics,
one explicit executor registration, a verifier, and a separate policy. `open_runtime`
holds the single-owner lock until its context exits and performs existing recovery.
There is no package scanning, model-selected executor, global registry, or implicit tool.

The compiler admits only an exactly bound `Approved` Run at version 5 with one SIDE_EFFECT
step matching the configured binding. The complete registry and capability policy identity
includes tool/version/target, adapter/revision, and UUIDv4 scope. The consumer's Run contract
reuses every existing lifecycle rule with a composed in-memory Plan resource. Bundled
schemas, SQLite DDL, legacy Plan semantics, and demo entrypoints remain unchanged.

## Authorization and durable Actions

A verified local bearer capability carries the configured subject, operations, and
binding. `LocalDemoAuthorization` checks that context and the trusted tool binding;
compiler/repository validation checks the exact approved Plan. A durable grant binds
policy, Action, and compiled specification; admission is not a grant.

Implemented execution states:

`Pending → Authorized → Executing → Succeeded | Failed | Unknown`

Expected-version CAS protects competing transitions. Mutation idempotency keys and
command receipts prevent replay from creating a fresh transition. The attempt, execution
claim, and audit data must commit before the gateway calls the executor once. No
control-plane database transaction is held across the asynchronous tool call.

The durable ledger persists Actions, grants, attempts, results, command receipts, and
audit links. SQLite connections enforce schema/foreign-key expectations and validated
row reconstruction. Busy/cleanup/commit ambiguity must not be mistaken for success.

## Effects, receipts, and recovery

Admission creates and persists a UUIDv4 effect key once; existing-Action admission reuses
it. Request `Idempotency-Key` values identify commands, not new effects. The compiled
identity also retains Run/approval IDs, approved version, step index, compiler revision,
Plan digest, and specification digest. `ExecutionSpec`
freezes tool/version, target, arguments, adapter identity/revision, and execution scope.
The legacy demo retains `action-compiler-v1`, `action-spec-v1`, and
`demo-note-payload-v1` exactly. External identities use persisted `action-compiler-v2`,
`action-spec-v2`, and `tool-payload-v1`; their payload hash includes **all six binding
fields** and canonical arguments. `ExecutionSpec.from_compiled` selects the payload
revision from the verified persisted compiler revision, never model output. The direct
Dummy retains its legacy correlation digest plus its own complete `dummy-record-v1`
store digest. No existing Action is rehashed or migrated.

The separate demo store atomically binds an effect key to payload/scope and a receipt:
identical replay returns the original receipt; conflicting payload or store identity
cannot overwrite it. Gateway outcomes must correlate to the effect key, scope, target,
and payload digest. Success requires valid receipt evidence. Confirmed no-effect failure
requires a recognized reason; exceptions, timeouts, malformed evidence, or ambiguous
commits become Unknown.

After predecessor/helper quiescence, recovery converts interrupted `Executing` Actions
to `Unknown` without calling the executor. There is no automatic retry or implemented
public reconciliation API. Although the domain names reconciliation transitions, the
current ledger does not admit those later transitions. Single-owner SQLite is not proven
distributed HA or universal exactly-once execution.

## ToolExecutor and isolated Dummy path

The port receives immutable effect data, an effect key, and an attempt deadline/cancel
context; it receives no control-plane database or approval credentials.
See [Adapter guide](ADAPTER_GUIDE.md) for outcome and receipt requirements.

`DeterministicDummyExecutor` is a direct contract/reference implementation. Its real
SQLite receipt store is owned by tests. It is not wired into production Action
registration, the browser UI, or the planning catalog.

The separate [consumer example](../examples/consumer/app.py) uses its own audit executor
and durable receipt store through the actual core Action gateway. It is distinct from
both the demo composition and the direct Dummy tests. The shared runtime accepts a trusted,
consumer-defined `ModelProvider`; the reference example instead uses deterministic
planning and scripted reviewed approval.

The bundled `LocalOpenAIProvider` prompt remains tailored to `service-demo` and
`arguments.service_id`. Passing a consumer capability does not make that built-in prompt
general-purpose. A non-demo consumer that needs model-based planning must provide its own
appropriate trusted `ModelProvider`; the runtime still applies the consumer's strict Plan
validation. The bundled browser UI's Action admission control also remains limited to the
demo `append_service_note/1` tool on `service-demo`. `Runtime.http_app()` exposes the
existing HTTP/API contracts but does not make that UI generic. Consumers may admit Actions
through the supported HTTP/API interface or implement their own UI. Subsequent
authorization and execution can use the existing Runtime capabilities, within the
startup-bound single-tool, exact-approval, and verified-capability boundaries documented
above. This reference integration is not production-grade multi-user authentication.

Consumer tools have a generic confirmed-effect result message; legacy demo results retain
their original message. Each consumer ledger must retain the trusted schemas/identities
needed to validate its historical Plans when upgrading host configuration.

## Actual HTTP surface

| Method and path | Purpose |
| --- | --- |
| `GET /` | Packaged browser UI |
| `POST /api/plans` | Stateless Plan preview |
| `POST /api/runs` | Create/plan a persistent Run |
| `GET /api/runs/{run_id}` | Read Run |
| `POST /api/runs/{run_id}/approval` | Approve/reject with expected version |
| `POST /api/runs/{run_id}/actions` | Admit Action, v2 only |
| `GET /api/runs/{run_id}/actions` | List Actions, v2 only |
| `GET /api/actions/{action_id}` | Read Action, v2 only |
| `POST /api/actions/{action_id}/authorize` | Authorize, v2 only |
| `POST /api/actions/{action_id}/execute` | Execute, v2 only |

Action reads and mutations require a verified bearer capability. Action mutations also
require a canonical UUIDv4 `Idempotency-Key` and the appropriate expected version.
Strict request schemas/origin checks do not accept authority from query parameters.
The UI renders server state and safe text; refresh/deep links perform reads, not mutation
replay. See [Security](SECURITY.md) for the local/demo trust limits.
