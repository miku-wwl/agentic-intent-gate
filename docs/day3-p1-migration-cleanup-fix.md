# Day 3 P1 SQLite v2 migration cleanup corrective patch

Task: `DAY3_P1_SQLITE_V2_MIGRATION_CLEANUP_FIX`.
Corrective result: **PASS**. Complete regression: **1153 unique PASS, zero failures/errors,
five live deselected**. P0=0; unresolved P1=0 in this corrective scope. This is not a
Final Freeze rerun or Git delivery. [Structured evidence](evidence/day3-p1-migration-cleanup-fix.json)
records measurements, historical hashes and the bounded cross-audit limits.

## 1. Baseline and authorized scope

Started on main, HEAD = origin/main = 3de7802a1266cc48fd94213b71816ad88d292d42.
Day 2 tag day2-final-freeze remains c7d3e1ee051af40c69ec33d888ecf16475a2d789.
Index empty; only two expected untracked second-freeze reports existed. Status, log,
diff/name/check and six report hashes were checked before implementation. No Git lock
or matching project implementation process was observed; repeated status checks found
no unexpected concurrent edits. Unrelated host processes were not terminated.

Only migration cleanup, its focused tests and these two new corrective records are
changed. No add, commit, push, fetch, tag, release or Final Freeze rerun.

## 2. Historical chronology and preservation

First failed freeze: docs/day3-final-freeze.md and evidence/day3-final-freeze.json.
First corrective PASS: docs/day3-p1-v2-run-cleanup-fix.md and its evidence JSON.
Second failed freeze: docs/day3-final-freeze-rerun.md and its evidence JSON, still
untracked/unstaged. Historical FAIL is reported history, not converted to PASS here.
All six before/after SHA-256 values are retained in the new evidence.

## 3. Original finding

D3-FREEZE-RERUN-P1-MIGRATION-CLEANUP: migrate_v1_to_v2 finally inspected transaction
state, rolled back and closed sequentially. A rollback/property error skipped close,
could skip remaining acquired connections, masked the safe primary error with raw
SQLite and retained a writer lock. Offline migration has no HTTP route; the CLI only
catches RunStoreError. Faulted CLI invocation itself was not run; library entrypoint
and the handler's source were inspected.

## 4. Independent pre-patch reproduction

Two fresh real v1 WAL databases, Approved/v5 rows, separately private new backups,
real sqlite3.Connection subclasses and retained strong references. Actual migration
executed the first companion DDL, then failed at CREATE TABLE actions; rollback
also raised before delegation. Both escaped OperationalError, no safe category,
rollback_attempts=1, close_attempts=0, still_in_transaction=true. Independent 50 ms
busy-timeout BEGIN IMMEDIATE / actual UPDATE / COMMIT writers were blocked.

Total reproduction durations: 0.517496 and 0.352565 seconds; blocked-writer durations:
0.096304 and 0.103052 seconds. Explicit diagnostic base rollback/close restored writer
success, exact original rows, version 1, three tables, integrity and valid v1 backup.
Temporary business directories removed. No model/tool/user DB/GC rescue.
Ignored fresh evidence: .runtime/day3-p1-migration-cleanup-fix/20261009T011414Z/prepatch.json.

Two new original-fault tests intentionally failed against unmodified production,
recorded separately as prepatch-tests.xml, before any production edit.

## 5. Root cause and transaction trace

Path/attestation/preflight -> acquire/configure -> BEGIN IMMEDIATE -> strict v1 and
history validation -> private backup -> separate read snapshot / real SQLite backup ->
backup validation/release -> six execute(DDL) additions -> legacy companion INSERT ->
v2/data validation -> PRAGMA user_version=2 -> validation -> COMMIT -> final cleanup.
execute, not executescript, preserves transactional DDL and user_version. Main except
normalized operation errors, but old finally was outside that normalization.

## 6. Minimal production patch

Only src/agentic_intent_gate/adapters/sqlite_v2_migration.py:migrate_v1_to_v2 changed.
Track primary error; for each non-null acquired handle nest close in finally around
inspection/rollback and collect cleanup failure without stopping the handle loop.
Already successfully released reader/backup references remain None, as before.
Early reader rollback/reader close/backup close faults also reach independent final
cleanup for all remaining handles. No shared-helper refactor, SQL or migration change.

## 7. Error precedence and diagnostic safety

Preserve exact RunStoreError category/object and cancellation/control-flow identity.
Ordinary operation/commit errors remain DATABASE_UNAVAILABLE. Ordinary cleanup-only
failure turns otherwise healthy completion into that same safe error, not success.
Secondary cleanup cannot replace primary error. Add only the fixed safe note
"Migration cleanup failed; inspect offline before retry." No raw SQL/path/fault detail.
All acquired handles are attempted even if another cleanup raises BaseException.

## 8. Rollback, close and physical-release distinction

Rollback/property failure cannot skip close or another connection's close. Real
close-acknowledgement fixtures release the SQLite handle before raising. Handle reuse
then raises ProgrammingError, independently of GC, and healthy second writers commit.
A deliberately refusing driver fixture proves the limit: close attempted, actual
handle still open, writer blocked, safe error returned; diagnostic base cleanup then
releases it. This is not misreported as product physical release or an avoidable
skipped-close defect. No underlying OS/driver refusal recovery guarantee is added.

## 9. Sixteen required fault cases

All sixteen passed in each focused run on isolated real disk databases.

| Case | Boundary | Durable outcome |
| --- | --- | --- |
| 1 | Clean v1 migration | v2, nine STRICT tables, exact old rows |
| 2 | Configuration before BEGIN fails | exact v1, safe error, close |
| 3 | DDL error / normal rollback | exact v1, validated backup |
| 4 | DDL + rollback throws | close attempted, writer released, exact v1 |
| 5 | in_transaction throws | close attempted, exact v1 |
| 6 | rollback + close acknowledgement throws | safe primary, physical release, exact v1 |
| 7 | acquisition fails | no nonexistent-handle cleanup, unchanged v1 |
| 8 | COMMIT fails before commit | exact v1 and backup, no partial tables |
| 9 | Actual COMMIT / acknowledgement lost | safe failure, actual v2 inspected, no replay |
| 10 | Close acknowledgement after success fails | safe failure, actual committed v2 |
| 11 | Explicit retry after confirmed rollback | new backup; previous backup unchanged |
| 12 | Invoke already-v2 source | typed schema denial, no changes/backup |
| 13 | Future schema version 7 | typed schema denial, no changes/backup |
| 14 | Partial companion INSERT then fault | exact v1, no partial companion rows |
| 15 | Legacy classification | two null-proof LEGACY_UNBOUND, admission denied |
| 16 | Migration / close / fresh reopen E2E | old history; genuine new bound approval; Action admission/reopen |

Additional 18 cases: two original rollback-fault repetitions, six primary identity,
six all-acquired-handle cleanup, three early snapshot-release faults, one physical
close refusal. Total added unique tests: 34, not 102 from the three repetitions.

## 10. Real SQLite writer proof

Retain every acquired faulty handle until after product close/independent writer
assertions. Independent source connection uses 50 ms timeout, BEGIN IMMEDIATE, a real
no-op UPDATE runs and COMMIT. The original defect is retested twice on fresh stores
per focused run, with exactly one rollback and one close on the writer and transaction
still active immediately before physical close. No sleeps/GC/fake connection results.
Each focused run and the full run records 37 writer probes: 36 actual successful
commits and one intentionally blocked physical-refusal probe (controller cleanup
then succeeds), plus 90 retained physically closed handle observations. Full largest
successful writer duration: 0.015011 seconds; no avoidable residual lock. These are
measured samples, not universal latency/driver-release guarantees.

## 11. Migration atomicity and history

Representative Approved/v5, Rejected/v5, AwaitingApproval/v4 fixtures have three Runs,
two decisions and fourteen audits. Compare every original row value, Plan JSON/state/
version, exact DDL/autoindexes and FK definitions. Reopen integrity/FK checks pass.
Pre-commit faults restore exact v1 version/schema/rows; no partial addition, orphan,
invented proof/Action or repaired history. Private backup preserves the exact original
schema/version/history and validates with strict v1 composition.

## 12. Legacy and genuine new approval

Success creates only two LEGACY_UNBOUND companions with null identity/digest/time;
all five Action-related tables are empty. Legacy Approved admission raises
LEGACY_APPROVAL_UNBOUND, does not mutate any ledger and cannot supply an executable
Action. Migrated waiting Run accepts a genuine new BOUND_V1 approval, verifies its
exact proof, admits one Pending Action and reopens through the Action repository.
Existing all-state/migrated-APPROVE/REJECT tests and package smoke also pass.

## 13. Uncertain commit and explicit retry

Actual commit then lost acknowledgement safely fails while fresh independent inspection
finds valid v2; it is never called rolled back. Close-after-success error likewise does
not undo a durable migration. Pre-commit error inspection finds exact v1. Only an explicit
operator test call after confirmed rollback retries with a different new backup; old
backup remains unchanged. No automated migration replay, guessed result or downgrade.

## 14. Previous-fix compatibility

Adjacent selected run: 99 PASS, 35 deliberately unselected expensive hardening cases,
42.878 JUnit seconds: existing v2 Run cleanup 30, approval binding/migration 66, Action
cleanup 3. sqlite_actions.py and sqlite_v2_runs.py unchanged. Full persistence,
authorization, gateway, Unknown, all 38 execution-hardening cases and 17 actual
Run crash/restart cases PASS in the completed full suite, not inferred from this subset.

## 15. Bounded SQLite cleanup cross-audit

Search all product SQLite acquisition/transaction/rollback/close sites; inspect caller
ownership, exception normalization and commit uncertainty separately. No broad refactor.

| Source / function | Classification | Evidence / risk |
| --- | --- | --- |
| sqlite_v2_migration:migrate_v1_to_v2 | REPRODUCED_DEFECT, corrected | Original twice + 34 current regression cases |
| sqlite_v2_runs:_transaction | ALREADY_SAFE | independent close; typed precedence; 30 current tests |
| sqlite_actions:_transaction | ALREADY_SAFE | independent close; safe unavailable; different Action precedence |
| sqlite_run_repository:_transaction | POTENTIAL_ISSUE_NOT_REPRODUCED | close nested safely; secondary raw cleanup may mask typed error |
| sqlite_schema:initialize_database | POTENTIAL_ISSUE_NOT_REPRODUCED | close nested; cleanup outside safe error translation |
| sqlite_v2_schema:initialize_database | POTENTIAL_ISSUE_NOT_REPRODUCED | sequential property/rollback/close might skip close |
| service_demo:provision_demo_store | POTENTIAL_ISSUE_NOT_REPRODUCED | sequential cleanup; raw error/skipped-close risk |
| service_demo:DemoNoteExecutor.execute | POTENTIAL_ISSUE_NOT_REPRODUCED | sequential cleanup; gateway preserves conservative Unknown after dispatch |
| sqlite_v2_schema:expected_objects | OUT_OF_SCOPE | DDL-only in-memory reference; context manager is not explicit close |
| sqlite_schema:open_existing/configure_connection | OUT_OF_SCOPE | caller-owned returned handle; caller audited |
| sqlite_action_execution:SQLiteExecutionRepository | ALREADY_SAFE | inherits Action transactions; no independent handles |
| sqlite_action_ledger:validators | OUT_OF_SCOPE | only caller-supplied connections |

Five static potential entries were not independently fault-reproduced or assigned a
severity in this bounded cross-audit. They are disclosed, not proved healthy, dismissed
as P2 or silently repaired. No new unrelated reproduced P0/P1. Cross-audit PASS means
this requested bounded audit is complete, not all SQLite paths universally hardened.

## 16. Three fresh focused repetitions

Each independent process/test root: 34 PASS, 0 failed/errors/skips; JUnit 24.738,
23.043, 23.096 s; console 25.02, 23.30, 23.33 s; parent wall 31.636, 30.008, 29.897 s.
Same final production/test bytes. Earlier development-only 31-case run had one
harness failure: DDL-only in-memory reference was wrongly counted as a migration
business handle. Narrow test interceptor excludes that reference; no product behavior
or acceptance assertion relaxed. That development result is not final regression.

## 17. Complete deterministic and browser regression

Historical floor 1119; added 34; collection 1158 total / 1153 selected / 5 existing live
deselected. Full result: **1153 unique PASS, 0 failures/errors/skips, exit 0**.
Console 974.56 seconds; JUnit 974.405; parent wall 982.363. No internal runner error.
All 126 browser cases PASS on installed headless Edge. Browser floor remains 126,
including 49 Action real-backend, 13 Run real-backend, 6 planning real-HTTP and 58
controlled frontend cases. Counts are subsets, not added again. No live Ollama.

Full invocation uses UTF-8/file-backed output, --capture=fd --color=no --tb=short,
new D:/Ollama/Temp/aig-migration-full-20261009T011414Z basetemp and ignored full.xml,
full.log, full-processes.jsonl. Propagate exit code; any internal runner error fails.

## 18. Quality and offline dependencies

Ruff check PASS; format check PASS (144 files); tracked diff --check PASS. Offline uv
lock --check and source dependency check PASS, 34 packages. Dependency lock/build
metadata/schema/resources counts unchanged. New source/test/docs checked with
git -c core.autocrlf=false diff --no-index --check against NUL: no whitespace output
(exit 1 is the expected new-file difference, not a whitespace failure).

## 19. Packaging and resource identity

Offline wheel/sdist build and fresh noneditable, hash-locked isolated installation PASS.
22 runtime dependencies plus project; installed dependency check PASS. package_check
outside checkout PASS: import from installed site-packages, 45 runtime/four schema
resources byte-identical, static UI, migration/legacy deny/new-bound approval,
admission/authorization/one effect/receipt/replay/reopen. Its actual browser is NOT RUN
(controlled HTTP probe); complete browser proof is separate. Package business directory
removed; isolated environment retained. Final documentation-inclusive archive refresh
PASS: wheel 53 files / sdist 181 files; all 49 resource bytes match; all six preserved
history files and four corrective source/test/docs match current bytes in sdist.
No archive links, traversal/cache/private-config/database/model-binary artifacts.
Final archive hashes are in ignored audit.json, avoiding self-referential JSON/sdist hashes.

## 20. Scoped security, artifacts and cleanup

180 candidate files, wheel/sdist and nineteen runner/telemetry artifacts scanned:
zero high-signal secrets/prohibited artifacts, zero nonuniform authority literals.
Hash-bearing dependency export separately scanned with context-aware secret patterns.
Fifty actual managed browser credentials checked dynamically, zero exposure; no traces/videos.
These are scoped hygiene checks, not exhaustive security certification.
All five browser/driver contexts and 127 browser HTTP servers closed; 71 Action child
records stopped/readers joined/listeners closed, plus sixteen adjacent/full Run HTTP
fixtures closed. All 206 distinct recorded ports rechecked: zero listeners. Final task
process matches zero; repository .runtime DB/WAL/SHM zero; no staged/generated DB.
Browser business directories and independent reproduction/package business directories removed.
External synthetic DB/WAL/SHM counts retained: pretests 4, development 60, focused
66/66/66, adjacent 104, full 867; isolated install environment retained. Raw XML/log/
telemetry/JSON hashes and exact root paths are in ignored audit.json. Initial ignored
audit needed explicit Python -X utf8 on Windows; rerun passed, without product/test edits.
No credential supplied to migration tests. Public .env.example contains local-only settings.
No user DB or Ollama touched; no broad deletion/kill. Benign ignored evidence, external
synthetic pytest databases and isolated install cache are retained, not claimed deleted.

## 21. Findings and limits

Original migration cleanup P1 corrected by current focused/full evidence. Newly found
P0=0; unresolved P1=0 within this corrective scope; five separately disclosed static
potential paths are neither certified safe nor assigned P2. Existing cooperative
SQLite soft timeout, local single principal/owner, production identity/RBAC/HA,
physical power-loss, universal exactly-once, retry/reconciliation/Unknown resolver,
multistep/cloud/MCP/competition adapter remain outside scope. Driver physical-close
refusal is demonstrated as a limitation, not disguised as successful lock release.

## 22. Final corrective gate and stop boundary

DAY3_P1_MIGRATION_CLEANUP_FIX=PASS

DAY3_P1_MIGRATION_REGRESSION=PASS

DAY3_SQLITE_CLEANUP_CROSS_AUDIT=PASS (bounded static scope above)

DAY3_FINAL_FREEZE=NOT_RERUN

No schema/user_version/DDL/state graph/approval identity/capability/policy/gateway/
executor/registry/UI/dependency edits; no live inference/cloud/MCP/Day 4 or Git delivery.
All mandatory corrective checks completed; the six historical hashes and empty index
are rechecked at closeout. STOP. A future independent Final Freeze remains separately required.
