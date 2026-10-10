# Architecture

The implemented system is a local, approval-gated runtime with two entry points and an
independent adapter-test lane. Source lives under `src/agentic_intent_gate/`.

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
    L --> E[ToolExecutor: DemoNoteExecutor]
    E --> S[Separate demo receipt store]
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

The registry accepts the concrete demo executor, not arbitrary adapters. The compiler
admits only an exactly bound `Approved` Run at version 5 containing one supported
`append_service_note/1` side-effect step. Adapter identity/revision and execution scope
come from trusted configuration, never model-generated authority.

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
The demo's frozen payload digest uses `demo-note-payload-v1`; the Dummy store additionally
binds its complete adapter identity using `dummy-record-v1`. These are not interchangeable
generic digest formats.

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
