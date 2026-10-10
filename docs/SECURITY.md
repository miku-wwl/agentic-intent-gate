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
compiler admits only the supported single-step demo note Plan, and the concrete registry
resolves only the bound demo executor. Approval, admission, authorization, and execution
are separate boundaries.

Tool identity/version, adapter identity/revision, target, and execution scope come from
trusted configuration. Immutable arguments cross the ToolExecutor port, not Run storage,
capability secrets, or arbitrary control-plane authority. Adapters must revalidate inputs,
limit targets, and return correlated outcome evidence. Strict HTTP parsing/origin handling
and safe browser text rendering reduce local misuse; they do not authenticate a human.

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
Dummy's full adapter receipt digest are distinct, revisioned identities.

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

Before adding an external API, review credentials, allowed destinations, payload privacy,
bounded failures, provider idempotency/receipt guarantees, and authorization integration.
Never log bearer tokens or unnecessary sensitive data. See [Adapter guide](ADAPTER_GUIDE.md)
and [Architecture](ARCHITECTURE.md) for the actual boundaries.
