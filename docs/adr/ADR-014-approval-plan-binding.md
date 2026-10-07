# ADR-014 — Bind Approval to Exact Canonical Plan Identity

## Status and precedence

Accepted architecture repair for Day 3 Prompt 1.5, 2026-10-08 (Pacific/Auckland). DOC-ONLY. Base: 5b4a75bb58e62e3671cac35926065b5f9d284275. This ADR amends the approval prerequisites of [ADR-013](ADR-013-action-ledger-execution-boundary.md), [the Day 3 freeze](../day3-action-execution-architecture-freeze.md) and [Prompt 2's plan](../day3-implementation-plan.md). The [repair evidence](../evidence/day3-prompt1_5-approval-plan-binding.json) records decisions and document checks, not implemented guarantees.

## Context and observed gap

Day 2 approval records a local decision, Run identity and versions, but no approved Plan digest or snapshot. Its audit payload records lifecycle facts and decision_id. SQLiteRunRepository._validate_history reconstructs historical states using the current Run.plan; valid replacement arguments can therefore pass that validation.

The preceding Prompt 2 audit used one real temporary v1 SQLite file: approve a single append_service_note/1 Plan A, replace only its canonical stored note with Plan B, and reopen/validate. Run remained Approved/v5, approval/audit rows and Run identity/timestamps were unchanged, and the changed Plan was accepted. No model/tool was called; the fixture was removed. This diagnostic was not rerun in this repair.

Classify that stopped attempt as CORRECTLY_BLOCKED_BY_ARCHITECTURE_GAP. Its reported gate was DAY3_PROMPT2_ACTION_PERSISTENCE=FAIL under the prompt's gate vocabulary; this repair does not rewrite that result as an implementation PASS or a Day 2 regression. No files changed and no partial implementation was committed. The gap was identified before Action execution existed.

## Security invariant

A new bound approval records exactly the authoritative validated Plan associated with the guarded approval transaction. Future compilation must prove that the current Plan has that same identity. Approved state, a valid current Plan or an approval's mere existence is insufficient. Missing, legacy, unsupported or mismatched binding means deny; no model call, repair, automatic rebinding or silent re-approval is allowed.

This binds proposal content, not authenticated human identity or proof of what pixels a person saw. The existing LOCAL_USER marker and expected_version API retain their Day 2 meaning. Trusted application ownership and Plan immutability before the approval transaction remain assumptions; this repair detects later inconsistency against retained binding evidence. Rewriting all context and recomputing all hashes with privileged DB access remains outside the frozen host trust boundary.

## Identity options

| Option | What it provides | Cost/limit | Decision |
| --- | --- | --- | --- |
| A: canonical full approved snapshot | Direct byte comparison and original content for investigation | Duplicates user Plan/note data already stored on Run; context linkage still needed | Reject for MVP |
| B: canonical digest with approval context | Exact-content comparison and cross-Run/decision binding with bounded evidence | Cannot recover the original Plan from a mismatch; depends on retained trusted evidence | Choose |
| C: snapshot plus digest | Both comparison and original-content retention | Redundant storage/validation for this admission invariant | Defer forensic snapshots |

Digest-only is sufficient for the minimum comparison invariant. It is not a signature, MAC, encrypted record or administrator-proof ledger.

## Canonicalization and exact digest inputs

First reuse strict Plan parsing, JSON Schema and trusted semantic/risk validation. Define canonical serialization C(value) as the repository's canonical_json convention encoded as UTF-8: sorted object keys, separators comma and colon without whitespace, ensure_ascii=False, allow_nan=False; no BOM, final newline or Unicode normalization. JSON escapes/key insertion order do not affect decoded identity. Array/step order, string code points and whitespace inside note/goal/target remain significant. No trimming, target rewriting or numeric coercion occurs. Duplicate keys, lone surrogates, NaN/infinity and floating-point forms are rejected by existing strict parsing. Booleans remain booleans; any schema-permitted number is a bounded lexical integer.

Plan identity input is exactly the object with keys identity_revision = canonical-plan-v1 and plan = the complete validated Plan.to_dict(). plan_digest is lowercase hexadecimal SHA-256 of C(that object), with hashlib from stdlib in future implementation. Identical validated Plan values produce identical bytes/digest independently of Run, caller, key order and runtime location.

All current Plan fields are bound: goal, target, ordered steps and risk_class; every step includes tool_name, tool_version and the complete arguments object. No current Plan field is excluded, even goal text that does not change the adapter's operation. This avoids a second interpretation of the reviewed proposal. Extra unknown Plan metadata is rejected by the strict schema. Intent, HTTP headers, credentials, database path, timestamps and local approval reason are not Plan fields and cannot change tool semantics under the current contract.

The authoritative approval_plan_digest is a second lowercase hexadecimal SHA-256 over C(the following exact context object):

| Key | Value source |
| --- | --- |
| identity_revision | Literal approval-plan-v1 |
| run_id | Server-loaded approval decision's Run ID |
| decision_id | Server-generated decision UUIDv4 |
| decision | APPROVE or REJECT from the accepted decision |
| run_version_before | Guarded AwaitingApproval version, exactly 4 |
| run_version_after | Committed terminal decision version, exactly 5 |
| decision_created_at | Decision's strict UTC created_at, equal to the resulting Run updated_at |
| plan_identity_revision | Literal canonical-plan-v1 |
| plan_digest | The content digest defined above |

There are no additional keys. The Plan content digest is independent of approval context; the approval digest intentionally differs for another Run/decision even with the same Plan. This prevents a copied binding from serving as another decision's evidence. actor_type must independently remain LOCAL_USER; reason retains its validated optional display meaning. Neither grants execution authority.

approval-plan-v1 pins both SHA-256 and these canonicalization/input rules. No separate algorithm-agility field is needed. Unknown revisions or noncanonical/invalid digest encoding fail closed; never infer an algorithm or normalize a stored digest. Changing the Plan schema or identity rules requires a separately reviewed identity revision, not reinterpretation of existing bindings.

## Authoritative storage

| Location | Assessment |
| --- | --- |
| approval_decisions | Naturally adjacent, but changing its DDL conflicts with preservation of the three v1 tables |
| audit payload | Trace/history, not the sole execution binding; adding fields would also change frozen Run audit validation |
| Run row | Mutable proposal storage cannot be its own independent approval evidence |
| Dedicated approval_plan_bindings | Choose: one authoritative insert-only companion record per decision, with v1 DDL/rows preserved |

The future STRICT approval_plan_bindings table has exactly these logical fields; physical SQL belongs to Prompt 2:

| Field | Contract |
| --- | --- |
| decision_id | Primary key and foreign key to approval_decisions.decision_id; no separately supplied Run identity |
| binding_status | Exactly BOUND_V1 or LEGACY_UNBOUND |
| identity_revision | approval-plan-v1 for BOUND_V1; null for LEGACY_UNBOUND |
| approval_plan_digest | 64 lowercase hexadecimal characters for BOUND_V1; null for LEGACY_UNBOUND |
| bound_at | Exactly the joined decision.created_at for BOUND_V1; null for LEGACY_UNBOUND |

Every persisted approval decision has exactly one companion row; no orphan, duplicate, mixed-null or contradictory binding is valid. The joined decision supplies Run/choice/versions/time to the hash context and must agree with Run and audit. Both newly accepted APPROVE and REJECT decisions are bound; REJECT never enables admission. Application ports expose only named insert-at-decision and read/validate operations, not binding update/delete/rebind. Status and field combinations, primary/foreign keys and digest bounds require DB constraints; cross-table equality/cardinality are also validated before commit and on open/read.

BOUND_V1 names identity revision 1 inside future SQLite schema v2; it never means that historical SQLite v1 approvals were bound.

## Approval transaction and duplicate behavior

Future explicitly selected v2 mode uses one short BEGIN IMMEDIATE transaction: load/revalidate authoritative Run/Plan/history; guard AwaitingApproval and expected_version=4; choose the server decision UUID and monotonic timestamp; compute both digests; insert the decision and BOUND_V1 companion; CAS Run to Approved or Rejected/v5; append the existing decision-linked Run audit event; validate all linked facts; commit once. Exact statement order may vary only within that transaction and its validated end state. No model or external effect occurs.

Failure anywhere rolls back Run, decision, binding and audit together. An unconfirmed commit returns the existing safe storage error, not a guessed success or an automatic replay. No committed v2 decision may lack binding, and binding must describe the same Plan/decision/version/time as that transition.

The external approval body remains decision + expected_version + optional reason. The server computes identities; a client digest/snapshot is not accepted authority and extra fields remain invalid. Healthy duplicate/stale decisions retain existing version/state conflicts and write nothing. A replay after corruption may encounter the binding validation error before the stale-version guard; it still cannot replace any digest, add a second decision or bind the now-current Plan. Day 2 approval does not acquire a new Idempotency-Key/replay-success contract.

## Legacy v1 policy and truthful migration

Choose OPTION A: all migrated v1 decisions, APPROVE and REJECT, receive LEGACY_UNBOUND companions with all proof fields null. The marker records absence of proof, not a fake historical timestamp or digest. Preserve every old Run/decision/audit row and all three DDL definitions. Do not hash the current legacy Plan as historical approval evidence, import diagnostic hashes as proof, or infer binding from Approved/v5 or audit text.

In the Day 3 MVP, a historical LEGACY_UNBOUND Approved Run is permanently ineligible for Action admission. Its state/version remain Approved/v5 and healthy legacy GET still returns the unchanged Run contract; exact historical Plan content is explicitly NOT PROVEN. It gets no Action merely by migration. Missing companions after v2 selection are corruption, not an implicit legacy exemption.

No re-approval/reset/rebinding operation is added to a terminal Run. To request an executable proposal, the operator creates a distinct Run in v2, reviews its own authoritative Plan and records a new explicit bound approval through the existing API. This is a new proposal/decision, not resurrection of the old approval; there is no automatic copying, planning or consent. A v1 AwaitingApproval Run migrated without a decision may subsequently receive a genuinely new BOUND_V1 v2 decision at its existing guarded version. Migration itself makes none.

Reject OPTION B (re-approve the existing terminal Run) because it changes frozen terminal/version/decision semantics. Reject OPTION C (later Action authorization substitutes for missing Plan approval) because approval and permission answer different questions. A separate historical-evidence import or consent-refresh design is deferred, not an MVP loophole.

Explicit offline migration adds approval_plan_bindings alongside the five Action concepts: nine total tables, with the original three preserved. Validate v1/history, obtain a consistent WAL-aware backup under stopped ownership, create all six additions, insert only LEGACY_UNBOUND companions for existing decisions, validate the complete v2 schema/data and set user_version=2 in one atomic transaction. Fresh explicitly selected v2 databases use the same nine-table contract with no approvals/bindings initially. Existing v2 open validates only; default v1 mode remains strict and rejects v2; partial/extra/corrupt/future schemas fail closed. No auto-upgrade, downgrade, reset or memory fallback is introduced.

## Immutability, reconstruction and compiler preconditions

Choose application-level immutable Plan/decision/binding records plus DB shape/key constraints and digest validation at both v2 read and execution-sensitive boundaries. Application-only immutability misses the reproduced stored-row inconsistency. Triggers alone can be bypassed and are unnecessary additional SQLite machinery for this minimum; mandatory digest checks cannot be replaced by triggers. No trigger contract is introduced here.

For BOUND_V1 Approved and Rejected Runs, v2 reconstruction verifies the binding against the current validated Plan before using that Plan to reconstruct historical states. v2 startup validates all companions and bound content before recovery/serving. Legacy records validate their explicit absence-of-proof status and existing lifecycle, without pretending their current Plan is the historical approved content. Pure reads never repair, increment versions or append history.

Future admission, including same-key receipt replay, runs in one gate transaction and must:

1. Apply the existing trusted caller/visibility contract when that transport is implemented; parse Run ID, request version and scoped key without client binding overrides.
2. Load the authoritative Run, Plan, decision, companion and history together. For a new admission require Approved/v5 and linked APPROVE with versions 4 -> 5, matching IDs/time and valid audit linkage. Accepted receipt replay retains its frozen receipt-before-CAS order, but never bypasses current binding validation.
3. Require BOUND_V1, the known identity revision and exact field combinations. Revalidate all Plan schema/semantics and trusted risk; recompute content and contextual approval digests and compare the latter to retained approval evidence. No missing/legacy/mismatched proof can reach compilation or return a trusted executable snapshot.
4. Only then compile the whole supported single append_service_note/1 SIDE_EFFECT Plan for service-demo, resolve exact server binding/scope, preserve arguments and derive the Action specification. Reuse canonical-plan-v1 as Action.plan_digest; include the linked approval revision/digest as derived inputs of spec_digest, alongside the existing Run/decision/version/compiler/tool/target/arguments/risk/binding/scope inputs.
5. Persist Pending/v1 Action + creation event + accepted receipt atomically. Keep the existing immutable Action field set: approval_decision_id references the authoritative companion; no duplicate approval-binding column/snapshot is needed on Action. On reopen/replay/query of trusted Action data, validate that reference, content digest and recomputed spec_digest again. Run.version is unchanged by admission.

Later authorization and execute gates must revalidate that same approval/spec linkage before permission/claim, then separately verify current principal/policy against the exact Action spec_digest. They cannot repair binding or treat an unbound approval as consent. Authorization != Approval; Approved != Executed.

## Audit and safe error model

Keep the existing Run audit DDL, payload shape and version chain. Its decision_id already supplies the reference decision -> companion -> exact identity; traceability resolves that join. There is no duplicated digest in Run/audit, no full Plan/note in events and no new claim that human-readable logs are execution truth. Action events keep their independent spec references.

| Binding failure | Future admission error / fixed message |
| --- | --- |
| Missing companion or required proof | 409 APPROVAL_PLAN_BINDING_MISSING / The approval has no exact Plan binding. |
| Explicit historical status | 409 LEGACY_APPROVAL_UNBOUND / The historical approval has no exact Plan binding. |
| Validly shaped proof disagrees with content/context | 409 APPROVAL_PLAN_MISMATCH / The stored Plan does not match the approval binding. |
| Unsupported identity revision/status/digest format | 409 APPROVAL_PLAN_BINDING_UNSUPPORTED / The approval binding format is not supported. |

After valid Run/decision/history linkage, binding classification is deterministic: missing companion or null required BOUND_V1 proof -> MISSING; correctly shaped LEGACY_UNBOUND with all proof null -> LEGACY; unknown status/revision, malformed non-null proof or contradictory legacy proof -> UNSUPPORTED; otherwise a recomputed content/context mismatch -> MISMATCH. Foreign-key/cardinality/history corruption retains the safe storage category. Classification does not turn an inconsistent record into legacy status or authorize a repair.

Malformed Plan, Run/decision/history corruption, schema/I/O failures retain the existing safe validation/storage categories. Internal binding validation preserves a typed failure for the admission coordinator; generic v2 Run reads map invalid binding to existing DATABASE_UNAVAILABLE (503), and startup fails closed with its existing safe storage failure instead of serving. A rejected startup cannot promise an HTTP 409. Healthy LEGACY_UNBOUND is not startup/read corruption, but is always an admission denial. Later HTTP exposure belongs to Prompt 3. Error messages contain no digest, SQL, path or raw Plan.

## Threat cases T1-T9

| Case | Expected frozen handling |
| --- | --- |
| T1: approved Plan A replaced by valid Plan B | BOUND_V1 content/context recomputation differs; reject read/admission without rebinding |
| T2: same tool, changed arguments | Exact argument values remain in canonical identity; reject mismatch |
| T3: changed target/resource | Schema/semantic target check or identity mismatch rejects before compilation |
| T4: reordered steps | Order is bound; mismatch rejects even if the Plan is otherwise valid; MVP also rejects any multi-step Plan as a whole |
| T5: inserted executable step | Full Plan digest and whole-Plan shape checks reject; never compile a subset |
| T6: changed non-execution metadata | goal and risk_class are still bound; mismatch rejects; unknown extra fields fail schema validation |
| T7: approval/binding copied between Runs | Run/decision FK/history links and contextual digest bind IDs/versions/choice/time; copy fails; wholesale privileged recomputation remains the host trust limit |
| T8: historical v1 Approved Run admission | Valid LEGACY_UNBOUND is retained/displayable but returns LEGACY_APPROVAL_UNBOUND with zero Actions/calls |
| T9: duplicate approval after Plan mutation | Bound v2 read detects inconsistency; no write or new binding; healthy duplicates retain existing CAS/state errors; legacy duplicates cannot become bound |

These are architecture obligations and future tests, not tests passed by this repair.

## Prompt 2 restart and consequences

The architectural stop is resolved only at DOC-ONLY level. A separately requested Prompt 2 implementation must first add the explicit v2 companion schema, truthful legacy migration, named atomic v2 approval persistence and pure contextual identity validation. Then implement compiler/admission on that proof. Its old requirement that a migrated v1 Approved Run be executable is superseded: it must be denied; eligibility is demonstrated using a genuinely new v2 approval. Default v1 behavior/tests and all historical evidence stay unchanged.

Before implementation PASS, require real v1 fixtures for every Run state, preserved rows/DDL and all legacy decisions; fresh/migrated AwaitingApproval v2 bound APPROVE/REJECT; replay/no-rebinding; failure injection across decision/binding/Run/audit/commit; missing/orphan/wrong-revision/copied/context/content binding; T1-T9; pure reads/reopen; no Actions on migration; same-key/different-key/concurrent admission and receipt atomicity; the original 686-case deterministic floor plus packaging/quality and no P0/P1. No authorization transition, capability verifier, dispatch, adapter or HTTP Action handlers are pulled into Prompt 2.

The repair changes future eligibility and adds one future table, not the Run state machine or external approval schema. It cannot manufacture the missing historical proof. Snapshot retention, authenticated human/distinct consent, legacy re-approval/import, schema/identity evolution and protection against privileged rewriting need separate future contracts.
