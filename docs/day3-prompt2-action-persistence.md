# Day 3 Prompt 2 — exact approval binding and durable Action foundation

Final gate: **DAY3_PROMPT2_ACTION_PERSISTENCE=PASS**. Final complete regression: **850 passed, 0 failed/errors/skipped, 5 live deselected** (686 preserved + 164 new). Ruff/format/diff/lock, offline wheel/sdist/install and 33 runtime + 4 schema resource checks passed; P0=0, P1=0. All 77 deterministic browser cases and server/browser/port teardown passed. Environment policy blocked recursive deletion before execution; the three task-owned temporary directories were moved intact outside the repository for recoverable, non-blocking retention, not deleted. Results/packages remain ignored in .runtime/day3-prompt2; no test DB remains there. No commit/push/tag/release.

Review base: `662cd32814cc67b4f9b094ae4aa00e340da4511b`, `main`.
Authority: [ADR-014](adr/ADR-014-approval-plan-binding.md), [Day 3 freeze](day3-action-execution-architecture-freeze.md), and [implementation plan](day3-implementation-plan.md).
The first attempt remains **CORRECTLY_BLOCKED_BY_ARCHITECTURE_GAP**. This implementation supplies the prerequisite; it does not retroactively change Day 2 guarantees. Actual results/chronology: [implementation evidence](evidence/day3-prompt2-action-persistence.json).

## Approval identity, atomicity and reads

`domain/approval_binding.py` hashes the exact ADR-014 canonical UTF-8 objects with stdlib SHA-256. canonical-plan-v1 wraps the entire validated Plan.to_dict(); approval-plan-v1 binds precisely the nine frozen context keys. Sorted keys, compact separators, ensure_ascii=False, no newline/Unicode normalization/coercion, ordered steps, strict lexical integers and existing schema/semantic/trusted risk checks apply. Goal and every argument remain bound. Reason is not an execution input. These digests are integrity evidence, not signatures, authenticated consent or administrator-proof protection.

`SQLiteV2RunRepository` explicitly reuses named v1 Run operations. The existing BEGIN IMMEDIATE approval transaction inserts the companion after decision/CAS and before audit/end-state validation. Both APPROVE and REJECT receive BOUND_V1. Run/decision/binding/audit commit or roll back together; uncertain commit returns safe failure, not guessed success. Healthy duplicate/stale decisions preserve existing conflicts; corrupted reads/replays never rebind. Approval request/Run response are unchanged; client digests/extra fields remain invalid.

Terminal reads first validate decision/lifecycle projection **without** treating current Plan as historical proof, then verify binding, then run the original full reconstruction. Startup validates all Runs, companions and Pending ledger before recovery/serving. Generic Run reads translate invalid binding to DATABASE_UNAVAILABLE; admission retains typed missing/legacy/unsupported/mismatch failures. Healthy legacy reads remain compatible, with exact historical Plan content NOT PROVEN. Reads never repair data, append events or increase versions.

## Nine-table SQLite v2

The default `SQLiteRunRepository` and `aig-plans` composition remain strict v1 and reject v2. Explicit `SQLiteV2RunRepository(path, validator)` selects v2: fresh empty initialization or existing-v2 validation, **not** v1 auto-upgrade. No default runtime/provider/timeout change was made.

| Preserved STRICT tables | Added STRICT tables |
| --- | --- |
| runs, approval_decisions, audit_events | approval_plan_bindings, actions, action_authorizations, action_attempts, action_command_receipts, action_events |

The original DDL is imported unchanged. Version 2 requires exact normalized schema objects/autoindexes, WAL, FULL synchronous, foreign keys and 250 ms busy timeout. Constraints bound JSON, versions, companion proof/null combinations, one Action per Run-step, scoped effect identity, one future grant/attempt per Action, scoped receipt keys and one event per Action/version. There is no UNIQUE(run_id), snapshot, duplicate approval-binding column, reset/downgrade or memory fallback. Future/extra/partial/altered schema fails closed. The temporary in-memory DDL reference used to determine exact index objects holds no business data and is not a fallback repository.

## Explicit offline migration and rollback

1. Stop **every** application/helper owner. A write lock does not prove reader/process quiescence; `--owners-stopped` is an operator attestation, not an automatic ownership detector.
2. Choose the actual v1 DB and a **new**, distinct backup file in an existing trusted local directory. Existing backups are never overwritten.
3. Run `.venv/Scripts/python.exe -m agentic_intent_gate.adapters.sqlite_v2_migration DATABASE BACKUP --owners-stopped`.
4. Under a write lock, validate exact v1 schema/integrity/history. Create a private backup (Windows inherited ACL removed/current SID only; POSIX 0600). SQLite backup from a separate consistent read snapshot includes committed WAL pages; validate the backup before source DDL changes.
5. One transaction creates six additions, inserts only LEGACY_UNBOUND/null-proof companions for existing decisions, validates v2 structure/data, sets version 2 and commits. Original rows/DDL/Plans/versions/events are untouched; no Action/new decision is created.
6. Reopen through explicit v2 composition. The default v1 binary correctly rejects the upgraded path.

After failure/unconfirmed commit, inspect offline; do not guess success or retry automatically. A backup made before a later fault is retained for investigation. Rollback requires stopped ownership, frozen v1 code and the validated consistent v1 backup, preferably a new restore path. Never reuse the source's WAL/SHM, decrement user_version or delete v2 tables as a downgrade. Fixtures verify rollback by opening the backup with the strict v1 repository and verify rejection of the migrated source by that same repository.

Migrated APPROVE/REJECT decisions remain LEGACY_UNBOUND with null revision/digest/time. Legacy Approved/v5 cannot admit, reset or reapprove. Create a **distinct new v2 Run** and record its own new bound approval. A migrated AwaitingApproval/v4 Run without a decision may receive a genuinely new v2 APPROVE or REJECT.

## Action domain and trusted compiler

Action persists the frozen 18 immutable fields: 15 CompiledSpec fields plus action_id/effect_key/created_at, and separate state/version/time/result. Six states/allowed and forbidden edges are pure contracts. Only **Pending/v1 creation** is implemented. `guard_action` supplies independent not-found/version/source-state CAS preconditions, not a setter or dispatch permit. Future non-Pending rows/grants/attempts fail closed until Prompt 3 adds their named writers/validators. No authorization or execution is faked.

`ActionCompiler` verifies authoritative Approved/v5, linked APPROVE and BOUND_V1, complete Plan schema/semantics/trusted risk, and the entire single-step append_service_note/1 SIDE_EFFECT proposal for service-demo. Ready/intermediate/rejected/legacy, maintenance and every multi-step Plan fail as a whole. Metadata alone is not an executable binding: explicit server-owned ToolBinding identifiers and a stable provisioned logical execution_scope UUID are required. The default registry is empty; it contains **no callable/executor implementation**. Arguments preserve exact service_id/note text, with existing 1024-character note and 4 KiB canonical argument limits.

The canonical action-spec-v1 input contains identity_revision, all 14 pre-digest specification facts and derived approval_identity_revision/approval_plan_digest. It binds Run/version/decision/Plan/step/tool/target/arguments/risk/compiler/adapter/binding/scope. Action.plan_digest uses canonical-plan-v1; approval_decision_id joins the authoritative companion rather than duplicating it. Admission generates Action/effect UUIDv4 once. The bounded creation event retains effect_key as independent identity evidence, not note/Intent text.

Repeated pure compilation produces equal specs/digests but no effect identity. This follows the frozen plan's clarification of the prompt's “same effect identity during compilation”: persistence/replay/reopen prove effect-key stability. Query/startup validation recomputes retained facts/digests through validate_spec, **not** the new-spec compile entry point; it cannot replace stored evidence. Generic Run startup validates retained versioned ledger facts without provisioning an executable registry; the Action repository additionally requires the explicit server-configured compiler/binding.

## Internal admission and idempotency

`ActionService` / `ActionRepository` expose internal explicit admission and pure get/list. The receipt subject is an internal server input, **not** authenticated principal proof or a grant. No Action HTTP route, including reads, is wired; Prompt 3 must first supply verified capability/visibility checks. The existing Run approval HTTP API can be composed with v2 without changing its contract.

AdmissionCommand's strict body is only expected_run_version; Run UUID, internal subject and caller-stable UUIDv4 key are separately validated. A short BEGIN IMMEDIATE transaction reads scoped receipt `(subject, admit, Run, key)`, checks the canonical body fingerprint, reloads authoritative Run/Plan/decision/history/proof and compiles the eligible complete proposal. Exact accepted replay precedes CAS but always revalidates proof and immutable stored spec. Changed-body key reuse conflicts. A new key for the same Run-step can record an identical-existing admission without another Action/event/version. Different Runs remain intentionally distinct; there is no semantic Intent deduplication.

Action + creation event + accepted receipt commit atomically. UNIQUE(run_id, step_index), not UNIQUE(run_id), preserves 0..N cardinality while MVP admits one supported step. Reopen/query/replay validate canonical arguments, Plan/decision/proof/spec, effect identity, event and receipt linkage. Rollback leaves no partial facts; uncertain commit returns safe storage failure, with explicit lookup/replay to discover what survived. No model/tool/network I/O, external effect, automatic retry or fallback occurs.

## Test evidence and exclusions

The focused binding foundation passed before Action work. Two focused modules cover identity, both choices, all v1 states, migration/fault rollback, T1-T9, lifecycle pairs, ledger/spec tampering, receipt/concurrency/lock/TOCTOU and unchanged approval HTTP behavior. See evidence for measured counts and the complete deterministic/browser regression, offline packaging, secrets/artifacts and cleanup.

Decisive A→B fixtures preserve terminal v5 and approval rows while replacing semantically valid note/goal, reversing distinct note steps or inserting a valid step. Binding still rejects mismatch. Target/tool-version/risk mutations fail earlier schema/semantic checks; these are recorded separately. Missing/copied/malformed/revision/status/context proof fails closed, with no read/duplicate-decision repair.

No live Ollama, tool adapter, external note effect, Action grant/attempt/dispatch, retry/fallback/reconciliation, Turn, cloud/MCP, production auth or HA is implemented. Local DB initialization/backup/migration/admission are persistence, not tool side effects. Existing UI, four schemas, v1 code, frozen documents/evidence and Day 2 tag remain unchanged. No commit/push/tag/release is performed.
