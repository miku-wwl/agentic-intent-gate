# Day 2 Prompt 4 — recovery, idempotency and hardening

Base: `b197f4e9f1939fc33f1dfd62c51fe58ba0b62b86` on `main`, initially clean.
This checkpoint adds deterministic reliability evidence, not a product feature.
Final executed counts and the acceptance result are recorded in
[the evidence JSON](evidence/day2-prompt4-hardening.json).

Final gate: `DAY2_PROMPT4_HARDENING=PASS`. The current full deterministic run
passed 686 unique cases, with zero failures/errors and five live tests deselected.
The 622-case baseline gained 64 cases: 52 in the new module, 11 through repository
parameter expansion and one new subprocess barrier. Phase A passed 81 cases
(plus a two-case overlapping creation-strengthening recheck), Phase B passed 557,
and Phase D passed 686; these overlapping subset totals are not additive.

## Failure model and frozen boundary

The supported design is one local application owner with file-backed SQLite.
Tests cover abrupt process termination, repeated startup, competing decisions,
caller repetition, stale/malformed versions, bounded writer contention, injected
SQL/commit failure, corrupted persisted data and controlled provider failures.
They do not establish distributed ownership, HA, production durability under
hardware/power failure, authenticated authority or execution of any tool.

The eight states and schema version 1 remain unchanged. `Approved` means a local
approval record only, never Executed/Applied/Succeeded. There is no Action ledger,
execution worker, public audit endpoint, model retry/fallback, semantic Intent
deduplication or Idempotency-Key contract.

## Audit before edits

The private candidate matrix classified 25 cases: 11 PASS, 11 GAP, 2 OUT_OF_SCOPE
and 1 REDUNDANT. Existing tests already proved short per-operation transactions,
conditional state/version CAS, opposite-choice approval concurrency, sequential
duplicate decisions, strict stored-data validation, unsupported schema rejection,
all-state GET purity and real process restart recovery.

Actual gaps were coverage gaps: same-choice HTTP decision races, three recovery
passes with complete business-row comparisons, an already-PlanRejected killed
worker, Reject/pre-first-write rollback, the creation write-barrier lock matrix,
real SQL/audit/commit injection throughout creation, same-Intent overlapping
creation, exact waiting-Run malformed versions, additional persisted Plan/risk
attacks, invalid DB header/unopenable parent, and the complete transport-to-durable
Run provider failure chain. No product defect was demonstrated; no product source,
UI, schema, configuration or dependency change was needed.

New tests were added first and exercised the existing implementation. There was
no failing product regression to repair; a coverage gap alone was not treated as
permission to refactor working behavior.

## Recovery invariants

| Persisted state | First recovery | Later recovery | Audit |
| --- | --- | --- | --- |
| IntentReceived v1 | PlanRejected v2 | unchanged | one RECOVERY_REJECTED |
| PlanGenerating v2 | PlanRejected v3 | unchanged | one RECOVERY_REJECTED |
| PlanValidated v3, READ_ONLY | Ready v4 | unchanged | one RECOVERY_ROUTED |
| PlanValidated v3, either write risk | AwaitingApproval v4 | unchanged | one RECOVERY_ROUTED |
| Ready v4 | unchanged | unchanged | no new event |
| AwaitingApproval v4 | unchanged | unchanged | no new event |
| Approved / Rejected v5 | unchanged | unchanged | no new event/decision |
| PlanRejected v2 or v3 | unchanged | unchanged | no new event |

Each recovery matrix case runs three passes against real temporary disk storage.
After pass 1, all three business tables, Plan, timestamps, decisions and version
must be identical on passes 2 and 3. Recovery provider/planning calls are zero;
there is no tool executor to invoke. Only interrupted planning or trusted routing
may add one transition/event. No Run or Plan is regenerated.

The existing subprocess fixture is extended rather than replaced by an in-memory
restart: a parent kills a worker at a confirmed barrier (or inside an uncommitted
approval), then three fresh processes call the real product startup composition.
The serving function is observed by a fixture, not started as a network listener
in this lane. The full suite separately runs real HTTP/browser E2E.

## Decision concurrency and caller idempotency

APPROVE/APPROVE, REJECT/REJECT and APPROVE/REJECT use two request threads,
independent actual SQLite connections and a barrier before `BEGIN IMMEDIATE`.
The HTTP adapter is exercised through ASGI; this is not a socket/network race
claim. Exactly one HTTP 200 and one STALE_RUN_VERSION 409 are allowed. The winner
must agree with the single stored decision and successful audit, at version 5.

Existing sequential duplicate Approve/Reject tests remain authoritative: replay
of v4 after the terminal v5 result is stale; use of the current terminal version
is an invalid transition. Neither response changes decisions, version or audits.
New old/new/malformed version cases check an actual AwaitingApproval Run and
compare every business row before/after. Existing all-eight-state GET tests plus
new repeated failed-provider GETs prove read purity and zero model calls.

Two deliberate requests with the same Intent create two distinct Runs. The
concurrent test holds both requests at provider entry with a bounded shared
event, proving overlap; each calls the controlled provider once. This does not
relax the unchanged Prompt 3 single-operation duplicate-submit UI lock.

## SQLite atomicity and bounded failure

The frozen approval write order is Run UPDATE, decision INSERT, audit INSERT,
then COMMIT. Test-only connection subclasses inject before the first write,
after each of the three writes, and before commit, for both APPROVE and REJECT.
Each failure must roll back to exactly the prior waiting Run, zero decisions and
four audits. Existing uncertain-commit coverage still returns an error without
guessing success or replaying a transaction that may already have committed.

Creation has separate durable barriers, not one transaction around model I/O.
Twelve cases inject after the Run/state/Plan write, after its audit, or before
commit at each of create/start/save/route. Only the prior confirmed barrier may
survive: no Run, IntentReceived v1, PlanGenerating v2, or PlanValidated v3.
Fresh recovery then rejects the interrupted planning or routes the already
validated Plan, without calling the provider again. No Ready/Approved Run is
fabricated from a missing/unvalidated Plan.

Held real writer locks exercise create/start/save/route/approve/reject at the
repository, and create/approve/reject through HTTP. The existing busy timeout is
250 ms; each test requires completion below 1.5 seconds, safe 503 where applicable,
unchanged business rows and no provider call. WAL snapshot GET remains readable.
No production failure hooks were added.

## Fail-closed trust and forensic safety

Existing invalid JSON/history/schema corruption tests are retained. Additional
canonical persisted fixtures remove steps, substitute an unknown tool, corrupt
argument types or the trusted maintenance revision, and tamper both stored risk
metadata fields in either direction. Startup and GET reject the corrupt store;
the business rows remain unchanged. Trusted recomputation wins by rejecting an
inconsistent claim, **not** by silently rewriting it or regenerating a Plan.

Controlled invalid SQLite bytes and a file blocking the database parent path
fail safely while retaining forensic bytes. Existing future `user_version=2`
tests prove rejection without downgrade/reset/destructive migration. Tests
compare the original fixture after rejection; no user/production DB is opened.

Provider transport is HTTPX MockTransport, not live Ollama. Timeout, connection,
invalid envelope, missing content, envelope overflow, invalid JSON, schema-invalid
and semantic-invalid output each make one adapter/provider attempt and persist a
PlanRejected v3 Run with three contiguous audits and no decision/Plan/risk. HTTP
201 is the frozen accepted-Run contract; the embedded error is safe and GET is
stable. There is no retry, fallback or raw private-output persistence. Exact
safe error bodies and sentinel checks cover SQL/path/endpoint/private-output
leakage at the hardened HTTP boundaries.

## Verification, resources and deferred scope

Execution order: Phase A changed/new hardening tests; Phase B existing backend,
recovery, repository, service, API, provider and domain regressions; Phase C early
browser execution is unnecessary because no backend contract/source changed;
Phase D the complete deterministic suite, including all frontend/real HTTP
browser tests. Live markers remain deselected. Phase/subset counts overlap and
must not be added to the unique full-suite total.

Quality gates include Ruff lint/format, diff whitespace, lock consistency, build,
the existing isolated installed-wheel checker outside the source tree, resource
byte comparisons, secret and tracked-artifact checks, and process/listener
verification using actual subprocess/browser evidence. Tests use external
pytest-managed temporary DBs, explicit connection closure, bounded subprocess
termination and browser/server teardown. External pytest temp retention is
NON-BLOCKING when there is no owner/listener, tracked DB or user/production DB
impact; this is not an unresolved product P1.

Deferred: multi-owner/distributed recovery, production HA/power-loss qualification,
auth, execution/compensation/retry, public semantic deduplication and live-model
quality/load testing. None is part of this checkpoint's acceptance.
Prompt 2.5 and Prompt 3 evidence is preserved. No commit, push, tag or release is
performed; Prompt 4 changes are left for review.
