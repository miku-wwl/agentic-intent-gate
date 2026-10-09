# Day 3 P1 Action transaction cleanup precedence correction

Task: `DAY3_P1_ACTION_CLEANUP_PRECEDENCE_FIX`.
Recorded UTC: 2026-10-09T09:57:52.2099885Z.
This is corrective implementation and regression evidence, not a Final Freeze rerun.
[Structured evidence](evidence/day3-p1-action-cleanup-precedence-fix.json).

## 1. Starting Baseline

Live preflight verified branch `main`, HEAD, local `origin/main`, and remote main:
`d573598d21fce905fe16d0b528d275795ebb98c0`.
Day 2 `day2-final-freeze` still points to
`c7d3e1ee051af40c69ec33d888ecf16475a2d789`.
The initial index and tracked diff were empty. Exactly the two expected untracked
Rerun #2 FAIL reports existed; no additional worktree changes were present.
Their SHA-256 values were captured before editing. The same refs and empty index
were verified after the complete test run; no commit, push, tag, release or Git
history operation was performed.

## 2. Historical Freeze Rerun #2 Failure

The historical finding is
`D3-FREEZE-RERUN2-P1-ACTION-CLEANUP-PRECEDENCE`.
Its actual HTTP observation was stale Action conflict incorrectly becoming
503 `DATABASE_UNAVAILABLE` under cleanup injection instead of
409 `STALE_ACTION_VERSION`. This is a historical measurement, not a newly
executed pre-patch HTTP test. The fresh pre-patch reproduction below independently
confirms the repository classification defect. The original reports and counts
are untouched; `DAY3_FINAL_FREEZE_RERUN_2=FAIL` remains authoritative.

## 3. Original P1 Reproduction

Before modifying production code, the new stale-CAS regression was run against
the original helper on ten fresh file-backed SQLite fixtures:
two repetitions each of normal, rollback fault, transaction-state inspection
fault, physical-close/lost-acknowledgment fault, and rollback plus close faults.

| Cleanup | Actual original code | Rollback / close attempts per case | Writer |
| --- | --- | --- | --- |
| Normal | STALE_ACTION_VERSION; same primary instance | 1 / 1 | PASS |
| Rollback raises | DATABASE_UNAVAILABLE; primary replaced | 1 / 1 | PASS |
| Inspection raises | DATABASE_UNAVAILABLE; primary replaced | 0 / 1 | PASS |
| Physical close, then raises | DATABASE_UNAVAILABLE; primary replaced | 1 / 1 | PASS |
| Rollback and close raise | DATABASE_UNAVAILABLE; primary replaced | 1 / 1 | PASS |

All ten retained connections were strongly held through the independent
second-writer UPDATE/COMMIT. All Actions remained Pending/v1, with zero grants,
attempts, notes, receipts and dispatch calls. This reproduces error precedence,
not a recoverable physical lock leak.

Command (repository root; `P5_TEST_PROCESS_REPORT` set to the matching facts path):

```text
uv run python -m pytest -q tests/test_action_cleanup_precedence.py::test_stale_authorization_keeps_primary_and_releases_lock --junitxml ".runtime/day3-p1-action-cleanup-precedence-fix/20261009T092857Z/reproduction/junit.xml" --basetemp "D:/Ollama/Temp/aig-action-precedence-20261009T092857Z-reproduction"
```

Expected pre-fix result: 2 passed, 8 failed, 0 errors, 0 skipped; exit 1;
pytest 5.75 s, JUnit 5.402 s. These intentional original-defect failures are not
post-fix regression failures. Raw evidence:
[JUnit](../.runtime/day3-p1-action-cleanup-precedence-fix/20261009T092857Z/reproduction/junit.xml),
[facts](../.runtime/day3-p1-action-cleanup-precedence-fix/20261009T092857Z/reproduction/facts.jsonl),
[log](../.runtime/day3-p1-action-cleanup-precedence-fix/20261009T092857Z/reproduction/pytest.log).

## 4. Root Cause

`SQLiteActionRepository._transaction()` reraised typed domain errors, but its
`finally` cleanup handler unconditionally raised a new storage error if rollback,
`in_transaction`, or close raised an ordinary exception. A finally-block raise
replaced the established primary error. The existing nested finally already
attempted close independently of rollback/inspection; that ordering was retained.

## 5. Actual Action Error Taxonomy

The source defines three distinct sibling `Exception` subclasses, not one shared
domain superclass: `ActionError`, `BindingError`, and `RunStoreError`.
Real transaction paths were exercised, rather than inventing unreachable errors:

- Action authorization/receipts: `STALE_ACTION_VERSION`,
  `IDEMPOTENCY_CONFLICT`, `ACTION_NOT_AUTHORIZED`.
- Execution claim/result guards: `EXECUTION_ALREADY_STARTED`,
  `ACTION_NOT_EXECUTABLE`, and stale Action version.
- Admission against a stale/missing Run: `STALE_RUN_VERSION`, `RUN_NOT_FOUND`.
- Isolated persisted-binding corruption during admission:
  `APPROVAL_PLAN_BINDING_MISSING`.

Untyped acquisition, write, validation or commit failures keep the existing safe
`ActionError(DATABASE_UNAVAILABLE)` classification. Existing HTTP mapping remains
unchanged: stale Action 409, storage unavailable 503, authorization denial 403,
and the corresponding Binding/Run mappings. No error taxonomy or public contract
was redesigned.

## 6. Minimal Production Patch

Only [sqlite_actions.py](../src/agentic_intent_gate/adapters/sqlite_actions.py),
and only `_transaction()`, changed: 13 inserted / 5 deleted lines.
The helper stores the established primary exception instance before rethrowing.
Untyped ordinary failures first become the same safe storage category. Cleanup
failure without a primary error still raises safe storage-unavailability.
With an established primary, ordinary cleanup failure adds only the fixed note
`Action transaction cleanup failed.` and leaves that primary authoritative.

The `BaseException` branch is narrowly justified: it immediately reraises the
same cancellation/control-flow object, preventing ordinary cleanup errors from
reclassifying it. It does not swallow cancellation, interrupt or exit signals.
A new control-flow signal raised by cleanup itself also propagates.
No transaction boundary, commit point, retry, schema, state, policy, gateway,
dependency, frontend or healthy neighboring SQLite owner changed.

## 7. Primary Versus Cleanup Exception Precedence

| Primary outcome | Ordinary cleanup fault | Observed result |
| --- | --- | --- |
| Known Action conflict | rollback / inspection / close / combined | Original code and instance preserved |
| Legitimate Binding / Run guard | rollback / inspection / close | Original typed category preserved |
| Untyped write / acquisition / commit fault | none or cleanup fault | Safe DATABASE_UNAVAILABLE |
| No primary failure; durable commit acknowledged | close acknowledgment fault | Safe DATABASE_UNAVAILABLE; no success fabricated |
| Primary CancelledError / KeyboardInterrupt / SystemExit | ordinary cleanup fault | Same control-flow instance propagated |
| Cleanup itself raises a new control-flow signal | no primary | New signal propagated, not converted |

Preserving a primary conflict does not assert successful rollback or universal
physical lock release. Public HTTP responses retain only the fixed public code
and message, never raw SQLite detail or the internal cleanup note.

## 8. Rollback / Close Safety

Close is still attempted from an inner finally even when transaction-state
inspection or rollback fails. The ten focused stale cases each observed one
close attempt. Rollback was attempted once unless inspection failed first.
Across all 68 focused cases, measured totals were 44 rollback attempts,
66 close attempts, 66 retained connections and two acquisition-failure cases
without an owned connection. All values were independently reproduced in the
complete regression run.

A deliberate wrapper that refuses actual physical close keeps a lock when
rollback also fails. Its bounded writer was correctly BLOCKED while the strongly
held original handle was still unreleased. Only after explicit test-controller
native rollback/close did the writer commit. This driver failure is a limitation,
not falsely labeled as guaranteed physical release. No garbage collection,
process termination or controller release was used before the recoverable-fault
writer checks.

## 9. Action CAS Atomicity

For the genuine stale authorization rejects, all ledger tables, Run/approval
tables and the separate downstream store retained identical persisted
row snapshots: Pending/v1, grants 0, attempts 0, notes 0, receipts 0, calls 0.
Real idempotency/policy/admission/claim/result conflicts likewise preserved their
pre-operation snapshots, including already legitimate grants/attempts.
Existing 20-contender authorization and execution CAS tests still pass.
A rejected mutation does not produce a grant, attempt, event or dispatch.

## 10. Real SQLite Second-Writer Verification

Every focused case uses isolated file-backed persistence and a separate
SQLite connection with a 0.05-second busy timeout. The writer runs
`BEGIN IMMEDIATE`, an actual single-row `UPDATE runs` verified by `changes()=1`,
then COMMIT. Retained fault-injected handles stay alive throughout the probe.

Initial focused probes: 67 PASS and one expected BLOCKED deliberate close-refusal;
the latter passes after explicit fixture teardown. In particular, rollback and
inspection faults each passed twice on fresh isolated databases, with the primary
stale exception instance preserved. No recoverable case left a measured lock.
The full suite reexecuted the same 68 cases and matched all 68 expected codes.
Raw post-fix [facts](../.runtime/day3-p1-action-cleanup-precedence-fix/20261009T092857Z/focused-pass/facts.jsonl) and
[full facts](../.runtime/day3-p1-action-cleanup-precedence-fix/20261009T092857Z/full/facts.jsonl) retain counts and writer timings.

## 11. Real HTTP 409 Versus 503 Verification

Real Uvicorn over loopback, real capability verification and isolated SQLite;
no mocked final response:

| Authorization case | Actual status / code | Rejected durable state |
| --- | --- | --- |
| Normal stale | 409 / STALE_ACTION_VERSION | Pending/v1 |
| Stale + rollback fault | 409 / STALE_ACTION_VERSION | Pending/v1 |
| Stale + inspection fault | 409 / STALE_ACTION_VERSION | Pending/v1 |
| Stale + physical close acknowledgment fault | 409 / STALE_ACTION_VERSION | Pending/v1 |
| Stale + rollback and close faults | 409 / STALE_ACTION_VERSION | Pending/v1 |
| Actual INSERT failure | 503 / DATABASE_UNAVAILABLE | Pending/v1 |
| Actual connection acquisition failure | 503 / DATABASE_UNAVAILABLE | Pending/v1 |

All seven rejected requests preserve all business rows, with grants/attempts/
notes/receipts/calls zero. Responses have exactly the fixed public error and
contain no injected private detail, traceback, SQLite class, capability or
database path. After each rejection a new explicit healthy command with the
correct version and valid capability succeeds with HTTP 200, Authorized/v2,
one legitimate grant and no attempt/effect. No automatic retry is involved.
All seven servers stop, release their listeners, and never invoke the provider.

## 12. Execution Repository Compatibility

`SQLiteExecutionRepository` inherits this helper; no execution-owner or gateway
code changed. New shared-boundary tests preserve stale claim, already-started
claim and wrong-attempt result errors despite rollback/inspection/close faults.
Existing execution tests verify one winning claim, one durable attempt, no
dispatch before acknowledged claim commit, no transaction across dispatch,
no stale terminal overwrite, idempotent replay and no duplicate effect.

The full suite includes 38 existing execution-hardening cases and 71 managed
worker records. Fresh process-termination windows A through G retain schema
and recover conservatively: A remains Authorized; B–E become Unknown;
F remains Succeeded; G remains Unknown. Window D/G retains the one already
committed external note/receipt without repeating it. Those existing hardening
cases are included in 1246, not an extra run or extra test credit.

## 13. Commit Uncertainty

| Operation / injected boundary | Durable observation | Caller / dispatch |
| --- | --- | --- |
| Authorization COMMIT fails before durability | Pending/v1, grant 0 | DATABASE_UNAVAILABLE |
| Authorization COMMIT occurs, acknowledgment lost | Authorized/v2, grant 1 | DATABASE_UNAVAILABLE; no rollback claim |
| Authorization COMMIT acknowledged, close acknowledgment lost | Authorized/v2, grant 1 | DATABASE_UNAVAILABLE; no false success |
| Claim COMMIT fails before durability | Authorized/v2, attempt 0 | DATABASE_UNAVAILABLE; dispatch 0 |
| Claim COMMIT occurs, acknowledgment lost | Executing/v3, attempt 1 | DATABASE_UNAVAILABLE; dispatch 0 |
| Claim COMMIT acknowledged, close acknowledgment lost | Executing/v3, attempt 1 | DATABASE_UNAVAILABLE; dispatch 0 |

Explicit healthy authorization lookup/replay retains exactly one grant after
an actual durable commit. Unconfirmed durable claims do not return dispatch
permission. Explicit predecessor-stopped recovery marks them Unknown, same-key
replays do not dispatch, and fresh-key Execute is denied. No new state,
automatic retry, reconciliation engine or exactly-once claim was introduced.

## 14. No Unauthorized Side Effects

All 68 new fault-injection measurements contain zero dispatch calls, external
notes and receipts. Already legitimate fixture grants/claims are distinguished
from grants/attempts prohibited by rejected mutations. Existing positive
execution/browser/package checks separately confirm one authorized effect
and durable receipt; they do not contradict zero-effect rejection checks.
Run approval, Action authorization and explicit execution remain separate.

## 15. New Fault-Injection Tests

[test_action_cleanup_precedence.py](../tests/test_action_cleanup_precedence.py)
adds 68 genuinely new cases; no existing test or skip marker changed.

| Category | New cases |
| --- | --- |
| Stale CAS: five cleanup modes, two fresh repetitions | 10 |
| Eight real Action/Binding/Run guards, three cleanup faults | 24 |
| Acknowledged success and authorization replay | 1 |
| Real partial write plus cleanup faults | 3 |
| Authorization commit uncertainty | 3 |
| Shared gateway claim uncertainty / no dispatch | 3 |
| Acquisition failure | 1 |
| Three primary control-flow types, four cleanup modes | 12 |
| New cleanup control-flow types | 3 |
| Actual physical close refusal | 1 |
| Actual HTTP stale/storage distinction plus healthy successor | 7 |
| Total | 68 |

Required matrix cases 1–6 map to success/stale tests; 7–8 to real business guards;
9–10 to partial-write tests; 11–13 to authorization/claim commit tests;
14 to acquisition; 15 to every writer probe; 16/19 to shared claim/result guards;
17 to stale/missing Run and binding admission; 18 to stale authorization CAS;
20 to snapshot/effect assertions throughout. Existing concurrency, terminal CAS,
recovery and DemoNoteExecutor tests supply complementary positive/race coverage.

A developmental run after the production patch had 35 passes and 33 failures:
27 were a new-test snapshot assumption omitting the immutable downstream
`resource` row, and six were test-controller access to HTTP-thread-owned SQLite
handles. Only this new test module was corrected: compare the complete resource/
notes/receipts snapshot, and permit cross-thread diagnostic access after the
HTTP request completes (never concurrently). The production patch was unchanged.
The failed developmental [log](../.runtime/day3-p1-action-cleanup-precedence-fix/20261009T092857Z/focused/pytest.log) and
[JUnit](../.runtime/day3-p1-action-cleanup-precedence-fix/20261009T092857Z/focused/junit.xml) are retained honestly, not discarded or
counted as final acceptance. Final focused run: 68 passed, exit 0, 46.17 s
(JUnit 46.129 s).

## 16. Full Regression

Python 3.13.9. Existing repository selection
`-ra -m 'not live'` is unchanged. Exact complete command:

```text
uv run python -m pytest -q --junitxml ".runtime/day3-p1-action-cleanup-precedence-fix/20261009T092857Z/full/junit.xml" --basetemp "D:/Ollama/Temp/aig-action-precedence-20261009T092857Z-full"
```

`P5_TEST_PROCESS_REPORT` was set to `.runtime/day3-p1-action-cleanup-precedence-fix/20261009T092857Z/full/facts.jsonl`.
Collection-only verification: 1246/1251 tests selected, five live deselected,
exit 0. Complete execution: 1246 unique passed, 0 failed, 0 errors, 0 skipped;
five live deselected; exit 0; pytest 1013.55 s, JUnit 1013.436 s,
parent elapsed 1021.5274051 s. The full JUnit independently has 1246 distinct
(classname, test-name) pairs. All original module counts equal the historical
1178-test inventory, plus the 68 new cases.

A separate 12-module adjacent run passes 474, exit 0, 169.86 s
(JUnit 169.835 s): Action persistence 98, authorization 21, execution 34,
binding compatibility 2, recovery 12, HTTP 59, approval binding 66,
v1 Run 79, v2 Run cleanup 30, migration cleanup 34, consolidation 25,
DemoNoteExecutor 14. Crash/concurrency hardening also passes in the full run.
Focused 68, adjacent 474 and browser 126 are overlapping/subset evidence,
never added again to 1246. This is corrective regression, not a freeze procedure.

Artifacts: [full JUnit](../.runtime/day3-p1-action-cleanup-precedence-fix/20261009T092857Z/full/junit.xml),
[full log](../.runtime/day3-p1-action-cleanup-precedence-fix/20261009T092857Z/full/pytest.log),
[adjacent JUnit](../.runtime/day3-p1-action-cleanup-precedence-fix/20261009T092857Z/adjacent/junit.xml),
[focused JUnit](../.runtime/day3-p1-action-cleanup-precedence-fix/20261009T092857Z/focused-pass/junit.xml).

## 17. Browser Compatibility

Fresh existing suite within the full run: 126 passed, exit 0, all using actual
Edge 154.0.4258.62. Breakdown: 49 Action UI and 13 Run UI cases use real HTTP/
SQLite; six planning backend cases use real HTTP; 58 controlled-response frontend
cases are UI evidence, not real execution proof. No frontend source changed.

Current assertions cover admission, separate authorization, explicit Execute,
stale-tab CAS/refresh, memory-only masked capability, reload/GET purity, Unknown
disabling Execute, no automatic dispatch and no duplicate effects.
Five browser instances and their drivers exited; 127 HTTP server records closed;
50 Action and 13 Run private business directories were removed.
All 50 managed Action credentials were checked against browser artifacts and
the full JUnit: no credential exposure.

[Fresh browser evidence](../.runtime/browser-evidence/20261009T093832282927Z/results.json).
The newly generated happy-path and Unknown screenshots were also visually
inspected: the successful receipt is visible, and Unknown warns against automatic
replay without exposing capability contents. Live Ollama and live-browser
model inference are NOT RUN and not substituted with mocked live claims.

## 18. Packaging and Security

Current measured quality: Ruff check PASS; Ruff format check PASS (149 files);
tracked diff whitespace check PASS; lock check PASS (34 locked packages).
Dependencies, schemas and static UI have no Git diff.

Fresh offline wheel and sdist were built after the code/test fix, before these
new closeout reports. Isolated noneditable install is outside the source tree
at `D:/Ollama/Temp/aig-action-precedence-20261009T092857Z-package/venv`.
Hashed locked runtime requirements installed 22 dependencies plus the project;
`uv pip check` reports 23 compatible packages. Existing `package_check.py`
passes from that external environment. Its installed imports are not editable
and all current source bytes match 45 runtime resources and four schemas.
Static UI bytes match, and controlled installed execution produces exactly one
note/receipt with replay/reopen safety. The package probe is HTTP/ASGI only,
NOT actual browser evidence; the preceding browser lane is separate.

| Archive | Members | SHA-256 |
| --- | --- | --- |
| Fresh wheel | 53 | de71d254d19f3f2e0aab942c9d2844f34b58a6b464273f11fd601a6654e2e3f6 |
| Fresh sdist | 187 | 8363131a7c31f712a5687d5cd79db63f705661db8a943d5d7738072300f0127b |

Both archives have all 45 matching runtime files and four matching schemas,
zero forbidden SQLite/DB/WAL/SHM, real .env, model, cache or credential files,
zero scoped high-signal secret hits and zero machine-specific runtime paths.
The fresh sdist includes the new focused test. New closeout reports were generated
after this build; archives are not falsely claimed to contain those later reports.

Scoped tracked/new-file and task-log scans check private keys, provider keys,
AWS/GitHub tokens, literal credential assignments and raw Bearer credentials;
no real credential was found. These are scoped checks, not a universal proof
that no secret can exist. `.env.example` contains only configuration examples.
Final source/delivery scan covers 188 files and task-text scan covers 21 files.
The initial broad Bearer scan flagged four existing negative-test parameter
occurrences in collection/JUnit identifiers: the fixed 64-zero and 65-a invalid
inputs from `test_missing_invalid_credentials`. Their actual source and exact
identifier context were verified; only those identifiers were narrowly
allowlisted, not arbitrary Bearer strings. No artifact or credential was
rewritten or deleted, and the resulting scoped scan has zero genuine hits.

Fresh final cleanup verifies 71 worker records with stopped/readers-joined/
listeners-closed/no-token-exposure flags, no recorded worker PID alive and
no matching active task process. All 226 recorded unique ports were probed
on loopback and checked against actual TCP listeners: zero open/listening.
37 auxiliary HTTP cleanup records are valid; all five browser/driver records
are closed. Raw [cleanup facts](../.runtime/day3-p1-action-cleanup-precedence-fix/20261009T092857Z/cleanup.json) and
[aggregated audit](../.runtime/day3-p1-action-cleanup-precedence-fix/20261009T092857Z/audit.json) are retained.
Ignored test artifacts and external temporary test/venv directories are retained
for inspection; unrelated processes and user files were not removed.

## 19. Historical Audit Integrity

All 12 protected historical files retain their pre-task SHA-256. The ten older
tracked records also exactly match their committed HEAD blobs. The two
pre-existing untracked Rerun #2 reports still exist, remain untracked/unstaged,
and retain these identical before/after values:

| File | Unchanged SHA-256 |
| --- | --- |
| `docs/day3-final-freeze-rerun-2.md` | 603b86dabb9d229a57b93e07d9e9503131527bf498a99205f457f1d55e329287 |
| `docs/evidence/day3-final-freeze-rerun-2.json` | 39406d5301976cf2950a543d23ad0e8ce0493ec22b161627cc1d954ff546bb26 |

All 12 before/after hashes are recorded in the new JSON. No historical failure,
finding, count, filename or gate was rewritten. Latest historical freeze:
`DAY3_FINAL_FREEZE_RERUN_2=FAIL`.

## 20. P0/P1/P2 Assessment

Current corrective validation: P0 open = 0; P1 open = 0.
The original Action precedence P1 is FIXED and CORRECTIVE_REGRESSION_VERIFIED,
not independently re-frozen or released. The intentionally failed pre-patch run
and corrected test-harness developmental run remain recorded.

P2 / explicit limitations: physically refusing SQLite drivers cannot guarantee
lock release; local capabilities are not enterprise RBAC/authentication;
single-owner local storage is not production HA; synchronous SQLite deadlines
are soft, not preemptive; Unknown outcomes have no automatic reconciliation or
retry; no universal downstream exactly-once guarantee. No Day 4, cloud/MCP
adapter or authorization redesign was implemented.

## 21. Final Corrective Gate

```text
DAY3_P1_ACTION_CLEANUP_PRECEDENCE_FIX=PASS
DAY3_P1_ACTION_PRECEDENCE_REGRESSION=PASS
DAY3_FINAL_FREEZE=NOT_RERUN
DAY3_FINAL_FREEZE_RERUN_2=FAIL (historical, unchanged)
```

Only four corrective delivery paths exist: modified Action helper, new focused
test, new Markdown report, new JSON evidence. They are left uncommitted and
unstaged for independent review. The two historical untracked FAIL reports
remain untouched. No commit/push/tag/release/final freeze/Day 4 action followed
this corrective verification.
