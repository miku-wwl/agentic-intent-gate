# Security model

This is a local/demo runtime, not production-ready security. Trust assumptions include
the local operator, installed Python code, the host/filesystem, and configured effect
stores. Keep HTTP on loopback, files private, and credentials outside source/evidence.
Application binding is restricted to loopback; this is not a safe public deployment model.

## Untrusted planning and human approval

LLM output is untrusted data, never an executable command or a source of authority.
Strict JSON/envelope limits, schema validation, semantic tool/argument checks, and a
deterministic risk gate reject unsupported Plans. No automatic repair, retry, or fallback
turns rejected output into execution. Validation cannot prove intent fidelity.

Runs persist state/version/audit data. Approval/rejection uses an expected version and
does not execute anything. SQLite v2 binds approval to the complete canonical Plan,
Run, and decision identity. Admission recomputes and checks the exact binding, rejecting
stale, changed, or legacy-unbound Plans. Hash equality is not authenticated human consent.
The local approval endpoints do not implement production identity/role authentication.

## Authorization and trusted execution

Action endpoints require a server-verified local bearer capability, including reads;
mutations also require strict expected versions and UUIDv4 idempotency keys. The verifier
checks the configured token without treating arbitrary client-provided subject/scope
claims as trusted. The capability is not a human login or general RBAC service.

`LocalDemoAuthorization` checks subject, operation, and execution binding. Compiler and
repository validation enforce exact approval. A persisted grant binds the policy revision
and compiled specification. The trusted
compiler admits one approved SIDE_EFFECT step with an exact trusted binding. The unchanged
demo resolves only its note executor; a consumer explicitly registers one host-owned
executor before serving. Approval, admission, authorization, and execution
are separate boundaries.

Tool identity/version, adapter identity/revision, target, and execution scope come from
trusted configuration. Immutable arguments cross the ToolExecutor port, not Run storage,
capability secrets, or arbitrary control-plane authority. Adapters must revalidate inputs,
limit targets, and return correlated outcome evidence. Strict HTTP parsing/origin handling
and safe browser text rendering reduce local misuse; they do not authenticate a human.

The consumer API uses a closed bounded scalar schema and a pinned tool/version/target;
optional semantic rules are trusted host callables, not model-selected validators.
`BoundCapabilityAuthorization` freezes one verifier-issued principal identity, operation
set, complete binding, and policy revision. A token does not grant authority over arbitrary
tools. The registry has no mutation/registration HTTP API, discovery, or global state.
Installed host/adapter Python code remains trusted: the runtime cannot sandbox malicious
code or prevent host code from calling its own executor outside the gateway.

## Trusted execution budgets

Five seconds remains the backward-compatible default authority. A host can explicitly choose
a finite built-in int/float in `(0, 15]`; booleans/coercible objects are rejected. Fifteen
seconds is the enforced maximum, not authority available from a model or HTTP request.
`open_runtime` freezes the value through the compiler and Gateway. Direct Gateway construction
enforces the same ceiling and checks compiler/profile compatibility.

Above five seconds, a reviewed binding revision must carry its exact canonical budget suffix
from `execution_budget_binding`. New Actions use compiler/spec revision v3 and existing durable
digest/grant/attempt links. Current binding **and compiler revision** must match before claim.
Default v1/v2 Actions remain readable/recoverable, but cannot inherit extended execution authority,
even when their historical revision name coincides with the suffix. Never edit persisted Actions,
relabel old effect stores, or transfer grants to a new profile. For a consumer upgrade, provision
a fresh scope and private ledger/store identity, obtain a fresh Plan review, and authorize its
new Action separately. Approval binds the Plan; it does not independently record a host budget
before admission. Consumers must present the reviewed execution profile alongside human consent.

Adapter time starts after durable claim and uses event-loop monotonic time. Caller deadlines
can shorten it, never enlarge it. Invalid values fail before claim; expiration after claim is
terminal Unknown. Pre-dispatch and post-return checks prevent late evidence being accepted
when synchronous work or swallowed cancellation delays asyncio's timer. Cancellation finishes
Unknown and propagates after durable finish; a finish failure remains a storage error requiring
fail-closed recovery. The Gateway creates no detached execution or retry task.

This is a cooperative execution limit, not a preemptive wall-clock or exactly-once remote effect
guarantee. Blocking code may return late; a provider may commit despite timeout/cancellation.
An acknowledged effect whose receipt handling crosses the deadline, or an effect whose
acknowledgement is lost, becomes Unknown. Consumers must reserve time inside the adapter
budget for provider verification and durable receipts, and additional request time for Gateway
claim/finish. A confirmed on-time receipt retains its classification if the later gate finish
is merely slow; an ambiguous/failed finish is never reported as a confirmed durable response.

## Persistence, concurrency, and duplicate effects

SQLite enforces schema/foreign-key expectations. Transactional writes and expected-version
CAS protect Run decisions, Action transitions, grants, claims, and command receipts.
An execution attempt must be durably claimed before one executor dispatch. A failed or
ambiguous claim commit cannot justify dispatch. No control-plane transaction spans the
asynchronous effect call.

Stable effect keys, exact payload/scope bindings, and atomic effect/receipt persistence
prevent duplicate local demo effects. Same identity replay returns the original receipt;
conflicting payloads/scopes fail without replacing prior results. Separate provider stores
need their own durable idempotency guarantees. This is not a universal exactly-once
promise for arbitrary external services.

Persisted results must match the claimed effect key, scope, target, and payload digest.
Success needs valid receipt evidence; confirmed failure needs recognized no-effect
evidence. Malformed/uncorrelated outcomes fail closed. The current demo digest and
Dummy's full adapter receipt digest are distinct, revisioned identities. External compiled
Actions retain `action-compiler-v2`/`action-spec-v2`; `tool-payload-v1` includes canonical
arguments plus exact tool, version, target, adapter, binding revision, and scope. Altering
any authority field changes the digest; a trusted registry also checks it independently.
Legacy demo and direct Dummy identities are not silently reinterpreted.

## Ambiguity, Unknown, and recovery

A timeout, exception, cancellation after dispatch, lost acknowledgement, or ambiguous
effect commit may mean the effect happened. Preserve `Unknown` rather than fabricate
success/failure or blindly retry. A successful effect with a failed control-plane result
commit also needs conservative durable handling.

Recovery requires stopped predecessors/helpers and the explicit single-owner protocol.
Interrupted `Executing` Actions become `Unknown` without executor dispatch. The current
runtime has no automatic retry or public reconciliation API; domain reconciliation names
do not imply a usable persisted/API workflow. A local OS lock and SQLite CAS are not
proven distributed fencing or HA.

## Boundaries and limitations

- Default `aig-plans` has no Action gateway. Execution requires explicit private v2
  configuration; startup does not silently migrate v1 data or provision effect stores.
- Local capabilities and operator approval are not production authentication, tenant
  isolation, or independently attested human authorization.
- The Dummy is an isolated reference with a trusted injected store and test-owned SQLite.
  It is not production-registered or exposed in the browser. UUID receipts are not
  cryptographic proof from malicious store code.
- Provider security, remote idempotency, credential rotation, network deployment,
  distributed HA, and operational incident/reconciliation procedures are not validated.
- General-purpose adapter registration, arbitrary tools, MCP, and multi-agent execution
  are not implemented.
- The supported consumer baseline is one startup-bound SIDE_EFFECT capability, not
  unrestricted tool routing, multi-target authorization, READ_ONLY dispatch, or a
  general schema/plugin framework. Use separate ledgers and private effect stores.

Before adding an external API, review credentials, allowed destinations, payload privacy,
bounded failures, provider idempotency/receipt guarantees, and authorization integration.
Never log bearer tokens or unnecessary sensitive data. See [Adapter guide](ADAPTER_GUIDE.md)
and [Architecture](ARCHITECTURE.md) for the actual boundaries.
