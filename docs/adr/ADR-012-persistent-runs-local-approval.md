# ADR-012 — SQLite Runs and versioned local approval without execution

## Status

Accepted for Day 2 Prompt 1 architecture/contract freeze, 2026-10-04, Pacific/Auckland. Implementation is not started. Normative details are in the [state architecture](../day2-state-architecture-freeze.md); [audit](../day2-repository-audit.md) and [Prompt 2 plan](../day2-implementation-plan.md) record evidence and implementation order.

**Implementation status note:** “not started” and the future-evidence statements below are the accepted Prompt 1 snapshot. Prompt 2 implemented the backend/recovery, Prompt 3 completed the local Run/approval UI, and Prompt 4 verified hardening. See the [final freeze](../day2-final-freeze.md). The accepted decisions and deferred execution/authentication boundaries remain unchanged.

## Context

Day 1 at `abd695a1085fe52d85ee66cfbaf68717a867eac1` generates and independently validates a stateless planning preview. It has no Run identity, store, approval route or recovery runtime. Stage 1's deferred Turn/Action execution design is broader than this local persistence slice. Its authenticated, distinct-approver, expiring Action consent cannot honestly be claimed by a local application with no authentication.

## Decision

One accepted Intent creates a server UUIDv4 Run. Store immutable Intent and independently validated Plan, current Run state/version, one possible local decision and a transactionally paired audit trail in stdlib SQLite. Use three STRICT tables, user_version=1, foreign keys, WAL/FULL, short BEGIN IMMEDIATE mutations and version CAS; keep model I/O outside transactions. All API/state/DDL/recovery choices are fixed in the normative document.

READ_ONLY routes to Ready; both write risks route to AwaitingApproval. One APPROVE/REJECT with expected_version records Approved/Rejected. Repeats and races conflict; there is no idempotency key, approval reversal or automatic retry. LOCAL_USER is a server marker, not an authenticated identity. Ready and Approved are terminal local gate outcomes, never execution states.

On startup, interrupted received/generating Runs become PlanRejected/RECOVERY_INTERRUPTED. A committed PlanValidated re-runs only deterministic policy. Other states remain unchanged. Unknown/invalid schema or data prevents startup; no resetting a database or fallback to memory.

## Precedence and preserved execution requirements

For the Day 2 local Run API only, this ADR extends ADR-011's stateless scope to persistence and local approval, implements the local direction of ADR-002 as a smaller RunRepository, and scopes ADR-004/005/007's full Action consent/idempotency/HTTP semantics to their later execution slice. All write-risk proposals now require a local decision, which is conservative relative to the preview's no-execution policy.

This does not weaken ADR-003/004/006's future execution guards: local Run approval is not a replacement for exact proposal binding, current authorization, authenticated distinct consent, expiry checks or claimed external I/O. It is not a Stage 1 Turn/Action or an executed effect. Future execution starts from persisted state and its separately reviewed gates; /api/plans never feeds execution directly. The cloud/reliability roadmap and its entry review remain deferred.

## Consequences

State and local decisions can be designed to survive restart without changing the trusted Day 1 model/validation boundary. An HTTP 201 represents a created Run even when its PlanRejected state records a failed generation. Failed storage confirmation returns a safe error/lookup handle, not invented state. Run creation can duplicate after lost responses; no exactly-once claim is made.

One process/worker per local DB is the supported deployment. No tool execution, authentication/RBAC, external database, distributed locks, worker, event sourcing or UI implementation is part of this prompt. SQLite restart/approval behavior requires future executable evidence; Day 1 tests alone cannot verify it.

## Rejected alternatives

Persisting raw model output as a Plan or accepting model approval flags bypasses validation/policy. Automatic generation replay hides interrupted work. Mutable approved Plans or last-write-wins decisions lose consent binding. Calling LOCAL_USER an authenticated actor overclaims security. Implementing the full Stage 1 execution journal, cloud storage, an ORM or a new UI exceeds this freeze.
