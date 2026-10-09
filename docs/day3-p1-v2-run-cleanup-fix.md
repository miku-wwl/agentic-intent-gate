# Day 3 P1 v2 Run cleanup corrective patch

## 1. CORRECTIVE_SUMMARY

`DAY3_P1_V2_RUN_CLEANUP_FIX=PASS` and `DAY3_P1_REGRESSION=PASS`.
`D3-FREEZE-P1-V2-RUN-CLEANUP` is corrected within the authorized transaction helper.
This is not a final-freeze review: `DAY3_FINAL_FREEZE=NOT_RERUN`.
[Structured evidence](evidence/day3-p1-v2-run-cleanup-fix.json) contains current
measurements and immutable raw evidence references. No commit/push/tag/release.

## 2. BASELINE_CONFIRMATION

Branch `main`; starting HEAD, origin/main and direct GitHub main lookup matched
`cfa371cd27ef833bac93edbf8d743f73851f7222`. Day 2 tag `day2-final-freeze` resolves to
`c7d3e1ee051af40c69ec33d888ecf16475a2d789`. Index empty.
Only pre-existing untracked paths were `docs/day3-final-freeze.md` and
`docs/evidence/day3-final-freeze.json`; their bytes and historical FAIL remain intact.

## 3. ROOT_CAUSE

Original `SQLiteV2RunRepository._transaction` acquired/configured the connection,
entered BEGIN/BEGIN IMMEDIATE, validated v2 schema, yielded the application mutation,
and committed. Its except clauses normalized ordinary failures, preserved typed
RunStoreError/BindingError, but its finally then inspected in_transaction, rolled back
and closed sequentially without protection. Inspection/rollback/close errors escaped
normalization and replaced the primary failure. Rollback failure skipped close and
retained a write transaction; the Run HTTP generic catch returned 500 INTERNAL_ERROR.

Before editing production, the historical minimal reproduction again emitted
OperationalError, close_attempted=False, still_in_transaction=True and a blocked
independent writer. Two newly written tests also failed against that source: a primary
storage error was replaced by raw rollback error; a real HTTP version conflict returned
500 instead of its frozen 409. These intentional pre-patch failures are not final-suite
failures. Both test-owned HTTP/SQLite resources were explicitly released afterward.

## 4. FILES_CHANGED

- `src/agentic_intent_gate/adapters/sqlite_v2_runs.py`
- `tests/test_v2_run_cleanup.py`
- `docs/day3-p1-v2-run-cleanup-fix.md`
- `docs/evidence/day3-p1-v2-run-cleanup-fix.json`

Ignored diagnostic logs/scripts/archives and isolated external test/install roots are
verification artifacts, not additional delivered source changes. No earlier report edited.

## 5. PRODUCTION_PATCH

Only `_transaction` changed. Track the primary exception locally; retain exact
RunStoreError/BindingError identity and cancellation/control-flow identity. Normalize
ordinary operation/commit errors to the existing safe DATABASE_UNAVAILABLE. In finally,
nest close independently around inspection/rollback. Secondary ordinary cleanup errors
cannot replace the primary exception; cleanup failure without a primary error returns
DATABASE_UNAVAILABLE, never a guessed successful result. Acquisition failure does not
clean up a nonexistent handle. All acquired connections receive a close attempt.

No new error taxonomy or retry. A failed/lost commit acknowledgement returns the safe
unavailable error without asserting rollback. An actual committed approval remains
Approved/v5; confirmed pre-commit failures reopen as AwaitingApproval/v4.

## 6. FAULT_INJECTION_RESULTS

All ten cases PASS on real isolated disk databases. Additional twelve cases preserve
exact RunStoreError, BindingError and CancelledError identity across normal, inspection,
rollback and close cleanup. Eight cases use actual loopback HTTP, not ASGI transport.

| Case | Injected boundary | Observed result / reopened facts |
| --- | --- | --- |
| 1 | Normal success | Connection closed; Approved/v5 |
| 2 | SQLite final-audit write fails | Safe unavailable; rollback/close; AwaitingApproval/v4 |
| 3 | Primary storage error, rollback succeeds | Same primary object; closed; AwaitingApproval/v4 |
| 4 | Primary storage error and rollback fail | Same primary; close still attempted; writer unblocked |
| 5 | in_transaction inspection fails | Close attempted; safe unavailable; committed Approved/v5 |
| 6 | Primary, rollback and close acknowledgement fail | Both attempted; primary retained; no raw SQLite; v4 |
| 7 | Close acknowledgement fails after work | Safe unavailable, not success; committed Approved/v5 |
| 8 | COMMIT fails before commit | No partial rows; rollback/close; AwaitingApproval/v4 |
| 9 | COMMIT succeeds, handle closes, acknowledgement lost | Safe unavailable; close still attempted; Approved/v5; no replay |
| 10 | Acquisition fails before returning handle | Safe unavailable; no cleanup attempt on nonexistent handle; v4 |

Close-fault fixtures actually release the SQLite handle before raising a synthetic
acknowledgement error. They prove independent close attempts and safe error handling,
not that an OS/driver which refuses physical close is guaranteed recoverable.

## 7. REAL_SQLITE_LOCK_RESULT

Temporary v2 WAL databases, actual sqlite3.Connection subclasses, and retained strong
references to every faulty connection; no garbage-collection rescue. In the rollback
fault, transaction state before close is true, rollback attempted once, close attempted
once, and using the handle afterward raises ProgrammingError. A second independent
connection performs BEGIN IMMEDIATE, an actual no-op UPDATE and COMMIT with a 50 ms
SQLite busy timeout. Twenty-nine full-suite observations pass; residual locks: zero.
Largest observed second-writer elapsed time: 0.004195 s. This is a measured sample,
not a universal latency guarantee. Fixture teardown is explicit even on failed assertions.

## 8. REAL_HTTP_RESULT

Actual FastAPI/RunService/v2 SQLite served by Uvicorn on owned ephemeral loopback sockets;
HTTPX network client with proxy environment disabled. No fault routes or tool composition.

| Scenario | Expected / actual HTTP | Safe code | Persisted state |
| --- | --- | --- | --- |
| Normal | 200 / 200 | None | Approved/v5 |
| Commit-before + rollback error | 503 / 503 | DATABASE_UNAVAILABLE | AwaitingApproval/v4 |
| Inspection error after commit | 503 / 503 | DATABASE_UNAVAILABLE | Approved/v5 |
| Close acknowledgement error | 503 / 503 | DATABASE_UNAVAILABLE | Approved/v5 |
| Commit-before error | 503 / 503 | DATABASE_UNAVAILABLE | AwaitingApproval/v4 |
| Commit-after acknowledgement loss | 503 / 503 | DATABASE_UNAVAILABLE | Approved/v5 |
| Commit-after, close, acknowledgement loss | 503 / 503 | DATABASE_UNAVAILABLE | Approved/v5 |
| Stale version + rollback error | 409 / 409 | STALE_RUN_VERSION | AwaitingApproval/v4 |

All PASS. Error envelopes match fixed messages exactly; no raw SQL/path/trace. Each
faulted connection allows another writer. All seven approval scenarios subsequently
accept an explicit healthy command with 200: same waiting Run after pre-commit failure,
or a new waiting Run after committed work. Old-version replay against committed work
returns 409 and adds no decision. Model calls in these fixtures: zero.

## 9. APPROVAL_ATOMICITY_RESULT

Pre-commit failures: byte-for-value row snapshots across all nine tables unchanged;
Run waiting/v4, zero decisions/bindings, four audits. Committed outcomes: Approved/v5,
one correctly linked decision, one BOUND_V1 proof, five audits. Reopening validates exact
binding/history. All Action/grant/attempt/receipt/event tables stay empty. Integrity and
foreign-key checks pass. Expected_version=4 replay is stale; version=5 repeated approval
is an invalid transition. Neither adds rows. No automatic replay or inferred approval.

## 10. SECURITY_REGRESSION

Existing exact-bound approval, migrated LEGACY_UNBOUND denial, Plan/binding tamper
denial, capability verification, Action authorization and protected execution tests PASS.
Digest algorithms, compiler, HTTP contracts and trust boundaries unchanged. The
installed-package smoke also verifies legacy denial, new bound admission, authorization,
one execution effect and receipt, replay and reopen with no second adapter call.

## 11. ACTION_REPOSITORY_COMPATIBILITY

Action repository/gateway unchanged. Existing three Action cleanup regressions and
all 38 execution-hardening cases pass in both selected and complete runs. Crash windows
A-G and active-owner H, Unknown preservation, lost-response replay, denial/no dispatch,
restart convergence and no automatic effect replay remain green. Historical evidence
is not substituted for these current results. Cooperative soft timeout limitation remains.

## 12. TEST_COUNTS

Historical baseline 1089; added unique cases 30; current final unique cases **1119**.
Failed 0; errors/internal errors 0; skipped 0; live deselected 5. JUnit has exactly 1119
unique named testcase records, all PASS. The browser subset is 126, not added again.
It includes controlled-response frontend lanes and actual HTTP/SQLite Run/Action lanes;
no live model. Edge 154.0.4258.53, five closed browser/driver contexts, 127 stopped servers.

Focused 30 pass (26.91 s); selected existing 331 pass (665.57 s); full 1119 pass
(console 924.19 s, JUnit 924.120 s, parent stopwatch 931.747 s, exit 0). Subsets and
repeated observations are not added to the unique count. Pre-patch two failures are
separate defect evidence. A minor test-fixture scoping cleanup precedes the final full
run; all 30 final-version cases pass there.

Exact full command (PowerShell, Python 3.13.9 / pytest 9.1.1):

```powershell
$env:P5_TEST_PROCESS_REPORT=(Join-Path (Get-Location) '.runtime/day3-p1-v2-run-cleanup-fix/20261008T222318Z/full-processes.jsonl')
$env:PYTHONIOENCODING='utf-8'
$runWatch=[System.Diagnostics.Stopwatch]::StartNew()
.venv/Scripts/python.exe -u -m pytest -q --tb=short --color=no --capture=fd --basetemp=D:/Ollama/Temp/aig-p1-full-20261008T222318Z --junitxml=.runtime/day3-p1-v2-run-cleanup-fix/20261008T222318Z/full.xml *> .runtime/day3-p1-v2-run-cleanup-fix/20261008T222318Z/full.log
$result=$LASTEXITCODE
$runWatch.Stop()
# Observed PYTEST_EXIT=0; WALL_SECONDS=931.747
```

Prior terminal I/O error is separate from the production P1. Its retained traceback
ends in pytest terminalwriter.flush after the aborted freeze run, not in the transaction
helper. With supported fd capture, UTF-8/no-color and file-backed output, the current
complete run including all subprocess cleanup does not reproduce Errno 22. This supports
an environmental/output-channel explanation; the old handle's exact OS-level failure
was not independently isolated, and subprocess cleanup is not established as its cause.
No unrelated test infrastructure was refactored; the old partial run remains incomplete.

## 13. QUALITY_GATES

Ruff check / format check / tracked diff check PASS; offline uv lock check and source
dependency check PASS. Wheel and sdist build offline from cached dependencies. Isolated
non-editable wheel install under `D:/Ollama/Temp/aig-p1-package-20261008T222318Z/venv`
uses locked runtime requirements with hashes; installed dependency check PASS.
Existing package_check runs outside the checkout: 45 runtime and four schema resources
byte-identical, planning/Run/binding/admission/authorization/execution/replay PASS.
Its actual-browser status is NOT RUN; browser proof comes from the full pytest run.

Final archive refresh includes the new corrective documentation without runtime changes.
Candidate scan: 175 tracked/nonignored candidate files; wheel 53 files; sdist 176 files;
49 runtime/schema resources in each, no prohibited cache/database/private-config/path
artifacts or high-signal secret matches. `.env.example` is an inspected public local-only
template, not private credentials. Thirteen current JUnit/log/child artifacts have zero
nonuniform 64hex authority-shaped values (one uniform invalid fixture). Browser dynamic
scan against 50 memory-only managed credentials PASS. These are scoped scans, not a
universal secret-detection claim. Final archive hashes are in ignored audit.json, avoiding
a self-referential sdist/evidence hash.

All 142 retained Action child records from selected + full runs stopped, readers joined,
listeners closed (not a test-count total). Eight focused and eight full new HTTP fixture
servers stopped; all browser business directories removed; installed smoke business
directory removed. All 264 distinct recorded ports rechecked: zero listeners. Final
task-worker/browser process check has zero matches. Unrelated processes/Ollama untouched.
Isolated synthetic pytest datasets, install environment and ignored review evidence are
retained, not represented as deleted and not tracked/staged/packaged.

## 14. PREVIOUS_FREEZE_REPORT_STATUS

Both pre-existing files remain untracked, unstaged, at original paths and exact SHA-256:

- `docs/day3-final-freeze.md`: `528dd6a65b45d65a4b91792f166a6a47bde107ef5ee0b7c327507da7507b4d98`
- `docs/evidence/day3-final-freeze.json`: `7f1662ca9976668ce2d3ce9f6d362e163153db3a4cd553fdf223bf2536fbbfe5`

Historical FAIL and incomplete-freeze observations are unchanged. Correcting this P1
does not retroactively pass that review or certify competition-adapter readiness.

## 15. P0_FINDINGS

Zero newly found in this corrective implementation/regression scope.

## 16. P1_FINDINGS

One original P1 corrected; zero unresolved P1 found in the completed corrective scope.

## 17. P2_DEFERRED

Existing synchronous/cooperative soft-timeout limits, production identity/RBAC and HA,
physical power-loss proof, distributed durability, universal exactly-once effects,
multi-step execution and external competition/cloud/MCP adapters remain deferred.
No guarantee when the underlying OS/driver itself refuses physical close. No new scope.

## 18. SCOPE_CONFIRMATION

No schema, migration, Run/Action graph, approval identity, compiler, executable registry,
adapter effect semantics, capability/authorization contract, UI or dependency changes.
No live Ollama/cloud; no new runtime resource; no automatic retry; no Day 4;
no stage/commit/push/tag/release. HEAD/tracking/direct remote and Day 2 tag unchanged.

## 19. FINAL_GATE

`DAY3_P1_V2_RUN_CLEANUP_FIX=PASS`

`DAY3_P1_REGRESSION=PASS`

`DAY3_FINAL_FREEZE=NOT_RERUN`

STOP. A new independent final-freeze task is still required; it is not performed here.
