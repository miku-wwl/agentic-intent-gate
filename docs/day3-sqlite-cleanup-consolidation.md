# Day 3 SQLite Cleanup Consolidation

## 1. Scope and Starting Baseline

Task: `DAY3_SQLITE_CLEANUP_CONSOLIDATION`. The change is limited to SQLite
connection/transaction cleanup and its regressions; it adds no feature stage.

- Branch: `main`
- Starting `HEAD` and `origin/main`: `3de7802a1266cc48fd94213b71816ad88d292d42`
- Historical `day2-final-freeze`: `c7d3e1ee051af40c69ec33d888ecf16475a2d789`
- Initial index was empty. The only initial worktree changes were the six
  expected migration-correction and second-freeze-failure files; no unrelated
  changes were found.
- No user database was opened. Fault injection used fresh temporary SQLite
  files under the test temp root.

## 2. Preserved Uncommitted Migration Correction

The reviewed migration patch remained untouched. The initial summary hash for
`tests/test_migration_cleanup.py` contained one extra `e`; the actual 64-character
SHA-256 below was independently re-read and matches the file whose last-write
time predates this consolidation. The other five pre-existing worktree hashes
also match exactly.

| Pre-existing path | SHA-256 |
| --- | --- |
| `src/agentic_intent_gate/adapters/sqlite_v2_migration.py` | `dcea9bb450273d8042b5e9cc840bd350c204e4266c4e35ac193eb2fe9ea84b7d` |
| `tests/test_migration_cleanup.py` | `4f599d2c2cb285ed1fbefc5ce4528862a8b0b774a137efec96568f3b47656355` |
| `docs/day3-p1-migration-cleanup-fix.md` | `b0c3f51642a09a5f1a01e103295645f713e80142deebff4852d5aa091fc6072a` |
| `docs/evidence/day3-p1-migration-cleanup-fix.json` | `b05c3a8d57741cfdced98f7e6e261ba8be1c8e699cee3dc2ca8fdb749a4b4f19` |
| `docs/day3-final-freeze-rerun.md` | `331b4f6dcec159bb7c67a831efb26137a3d5caa0970aff2550b11435a40a5257` |
| `docs/evidence/day3-final-freeze-rerun.json` | `f36787530e66a8e424e7f0e3a93bbc28e50f93a9bc68f3ac0c76470bbf64fa30` |

The two first-attempt historical files and two first corrective-pass files were
also unchanged:

| Historical record | SHA-256 |
| --- | --- |
| `docs/day3-final-freeze.md` | `528dd6a65b45d65a4b91792f166a6a47bde107ef5ee0b7c327507da7507b4d98` |
| `docs/evidence/day3-final-freeze.json` | `7f1662ca9976668ce2d3ce9f6d362e163153db3a4cd553fdf223bf2536fbbfe5` |
| `docs/day3-p1-v2-run-cleanup-fix.md` | `22b50bf8f1f8605f4d922b0b1e64374f86d477660dff74112b73f228d21f7d59` |
| `docs/evidence/day3-p1-v2-run-cleanup-fix.json` | `cad600abf789e01e4de2a3fe73fa0e67973e1f89dce3dae3a6418a5f1ffd33cf` |

The migration correction's independent-close, safe-error, no-fabricated-rollback,
data-preservation, legacy-approval and schema-version contracts were rerun in the
full current-worktree suite.

## 3. Historical Freeze Failure Timeline

1. `D3-FREEZE-P1-V2-RUN-CLEANUP` — the v2 Run cleanup defect was corrected and
   its historical corrective PASS remains unchanged.
2. `D3-FREEZE-RERUN-P1-MIGRATION-CLEANUP` — migration cleanup failed a later
   freeze attempt; that FAIL remains recorded as FAIL.
3. The migration P1 correction was prepared in the existing uncommitted
   worktree and preserved through this consolidation.
4. This work adds cleanup consolidation evidence only. No Final Freeze rerun,
   commit, push, tag or release was performed.

## 4. SQLite Connection Ownership Inventory

| Module/function | Owner and transaction | Cleanup/result | Evidence and classification |
| --- | --- | --- | --- |
| `sqlite_v2_schema.expected_objects` | Owns one in-memory DDL-reference connection; no business data | Now explicitly closes with `closing` | Directly reproduced missing close; fixed |
| `sqlite_v2_schema.initialize_database` | Owns fresh/existing v2 schema connection and DDL transaction | Independently attempts close even if state inspection/rollback fails; safe `RunStoreError` | Two fresh pre-fix lock reproductions; fixed |
| `sqlite_schema.initialize_database` | Owns fresh/existing v1 schema connection and DDL transaction | Independent close; cleanup no longer masks primary domain/storage error | Reproduced error-precedence defect; fixed |
| `sqlite_run_repository.SQLiteRunRepository._transaction` | Owns one v1 Run connection per operation | Independent close; preserves stale/domain error identity and safe storage classification | Reproduced stale-version error masking; fixed |
| `sqlite_v2_runs.SQLiteV2RunRepository._transaction` | Owns one v2 Run connection per operation; overrides base transaction | Existing independent close and primary-error handling retained | Existing v2 cleanup tests and full suite PASS; already safe |
| `sqlite_actions.SQLiteActionRepository._transaction` | Owns one Action repository connection per operation | Existing rollback/close behavior retained | Action cleanup, authorization, execution and crash tests PASS; already safe |
| `sqlite_v2_migration.migrate_v1_to_v2` | Owns writer, reader and backup connections | Existing per-handle cleanup loop retained | Existing migration cleanup fault matrix/full suite PASS; pre-existing patch preserved |
| `service_demo.provision_demo_store` | Owns isolated demo-store initialization connection and transaction | Safe `ActionError`; independent close after inspection/rollback errors | Two fresh pre-fix lock reproductions; fixed |
| `service_demo.DemoNoteExecutor.execute` | Owns downstream per-attempt connection and effect transaction | Independent close; cleanup uncertainty returns `UNKNOWN_OUTCOME` | Pre-fix lock reproduced; fixed without retry changes |

`sqlite_schema.open_existing` and `configure_connection` return/configure a
caller-owned connection; they do not acquire a hidden persistent handle.
`SQLiteExecutionRepository` inherits the Action transaction owner. The ledger
reader/validator uses caller-supplied connections. `single_owner.py` closes a
file stream, not SQLite. No other production `sqlite3.connect` owner was found
in the bounded source audit.

## 5. Two Reproduced Initialization Defects

Both reported P1 issues were reproduced twice on separate fresh on-disk files,
with a strong reference retained through a real independent writer check.

| Target | Pre-fix injection and result | Diagnostic cleanup proof |
| --- | --- | --- |
| `sqlite_v2_schema.initialize_database` | Fail `CREATE TABLE actions`; injected rollback raises. Each run: `OperationalError`, rollback 1, close 0, transaction retained, writer blocked for 0.0917s / 0.1129s. | Explicit base SQLite rollback/close released the lock; second writer committed. Schema remained empty, `user_version=0`, integrity `ok`. |
| `service_demo.provision_demo_store` | Let `INSERT INTO resource` execute, then inject failure; rollback raises. Each run: `OperationalError`, rollback 1, close 0, transaction retained, writer blocked for 0.0995s / 0.1120s. | Explicit base SQLite rollback/close released the lock; second writer committed. No partial tables/resource remained. |

After correction, both targets were repeated twice with rollback failure. Each
returned the intended safe storage category, attempted close once, and allowed
the independent writer to commit without relying on garbage collection.

## 6. Investigation of Three Suspicious Paths

- `sqlite_schema.initialize_database`: injected failure on
  `CREATE TABLE approval_decisions` followed by rollback failure. Before the
  fix, rollback 1 and close 1 occurred and a second writer committed, but raw
  `OperationalError` replaced the intended `DATABASE_UNAVAILABLE` error.
  Final: `REPRODUCED_DEFECT_FIXED`; public category preserved and no partial v1
  DDL remains.
- `sqlite_run_repository._transaction`: stale approval version plus rollback
  failure. Before the fix, rollback 1 and close 1 occurred and the writer
  committed, but raw `OperationalError` masked `STALE_RUN_VERSION`. The Run
  remained `AwaitingApproval`. Final: `REPRODUCED_DEFECT_FIXED`; domain error
  preserved.
- `DemoNoteExecutor.execute`: failure after the note insert and before receipt
  insert plus rollback failure. Before the fix, rollback 1, close 0, retained
  transaction and blocked writer (0.0983s); diagnostic cleanup showed the
  transaction had no committed note/receipt. Final:
  `REPRODUCED_DEFECT_FIXED`; close is attempted and result remains
  `UNKNOWN_OUTCOME` on cleanup uncertainty.

The additional in-memory `expected_objects` owner also had a direct pre-fix
reproduction: close attempts 0 and the retained in-memory connection remained
usable after the owner returned. It now closes explicitly; this never contained
business data or held a file lock.

## 7. Root Causes

- v2 schema initialization and demo provisioning placed state inspection,
  rollback and close in one sequential `finally` path. A rollback exception
  prevented `close()` from running.
- v1 initialization and the base Run repository did close in a nested `finally`,
  but did not preserve the already-normalized primary error across cleanup. A
  rollback error superseded the intended storage/domain classification.
- the demo executor used sequential inspection/rollback/close. A rollback error
  both skipped close and escaped the conservative `UNKNOWN_OUTCOME` return.
- `with sqlite3.Connection` controls transaction exit, not connection lifetime;
  the cached DDL-reference helper therefore lacked an explicit close attempt.

## 8. Minimal Production Corrections

Only independently reproduced cleanup owners were changed:

- `sqlite_v2_schema.py`: independently attempt close, preserve/normalize primary
  errors, and explicitly close the in-memory schema-reference connection.
- `service_demo.py`: wrap store initialization errors in safe Action categories;
  independently attempt close. Executor cleanup now independently closes and
  returns Unknown when cleanup fails after an otherwise reportable result.
- `sqlite_schema.py`: preserve the typed primary error and independently close
  after inspection/rollback failures.
- `sqlite_run_repository.py`: preserve the original Run domain/storage category
  and independently close.

No DDL, schema version, migration logic, executor schema, automatic retry or
generic transaction framework was introduced.

## 9. Exception Precedence

An existing safe/domain exception remains primary; cleanup failure adds only a
fixed, non-sensitive diagnostic note. SQLite/OSError failures map to the
existing safe `DATABASE_UNAVAILABLE` category. With no primary error, ordinary
cleanup failure is not treated as success. Control-flow `BaseException`
cancellation is propagated by the demo executor. Error messages do not include
the injected text, SQL, file path or credentials.

## 10. Rollback and Close Behavior

Close is nested independently from transaction-state inspection and rollback in
all corrected owners. The regression matrix covered successful rollback,
rollback exception, inspection exception, and rollback plus close exception.
Where the injected close raised only after the underlying SQLite close actually
completed, the writer succeeded and the safe primary classification remained.

A separate deliberate `close_mode=refuse` test showed rollback 1, close attempt
1, transaction retained and second writer blocked. The test explicitly records
that physical release was **not** claimed, then performs diagnostic cleanup on
that test-owned handle and confirms the writer succeeds. This is a limitation
of a deliberately nonfunctional underlying close, not a claim that application
code can force a driver to release a refusing handle.

## 11. Commit Uncertainty

Demo executor commit-before failure rolled back the uncommitted note and
returned Unknown; note/receipt counts remained zero. Commit-after (actual commit
then lost acknowledgement) and close acknowledgement loss each returned
Unknown while preserving exactly one note and receipt. An explicit subsequent
invocation with the same effect key returned the same durable receipt without a
second note. No automatic replay was added. Cancellation raised after actual
commit propagated; the durable effect was later found by explicit keyed replay.

## 12. Real SQLite Second-Writer Evidence

Pre-fix: four independent initialization repro databases plus the executor
reproduction retained transactions and blocked a bounded second writer. The v1
schema and base Run repository suspicious cases closed physically; their writer
commits succeeded, isolating the bug to exception precedence there. After fixes,
each ordinary cleanup-failure scenario in the targeted matrix allowed a separate
SQLite writer to execute `BEGIN IMMEDIATE`, write and commit. No connection
reference was dropped before the check.

The deliberate physical-close-refusal control remained blocked until the test
explicitly cleaned its retained handle. That result is reported as a physical
close limitation, not a false PASS.

## 13. Schema Initialization Atomicity

- v1: normal initialization contract remains schema version 1 with three
  unchanged STRICT tables. Injected failure plus rollback error left no partial
  DDL and preserved the safe storage category.
- v2: normal initialization is idempotent, version 2, nine STRICT tables and
  expected indexes. A failed first initialization left version 0/no tables;
  a fresh retry initialized successfully and a second explicit initialization
  produced the same snapshot.
- Migration: current working-tree migration correction and its data/legacy
  approval tests passed; no migration or schema resource was modified here.
- Partial durable schema/fact count in confirmed rollback tests: 0.

## 14. Demo Store Initialization

Fresh demo provisioning still creates the frozen version-1 resource/note/receipt
schema atomically. Failure after a real resource insert plus rollback failure
now closes the handle and leaves no partial store. Re-provisioning an existing
private file safely refuses at exclusive-create (`FileExistsError`) rather than
overwrite it; a pre-existing note and receipt were byte-for-byte unchanged.
This refusal is intentional non-overwrite behavior, not idempotent success.

## 15. Demo Executor Outcome Integrity

The stable Action effect key, strict compiled payload, target/tool restrictions,
note+receipt transaction, conflict detection and receipt replay remain intact.
Tests confirmed one note/receipt per key, `PAYLOAD_CONFLICT` for different
content, Unknown for unavailable/ambiguous results, and no automatic retry.
The actual per-test UUID key and receipt values are intentionally not copied to
the report; tests assert exact identity equality across the durable receipt and
explicit replay.

## 16. Previously Fixed Run/Action/Migration Compatibility

The complete deterministic suite ran against the actual working tree, including
the uncommitted migration fix. V1/v2 Run storage, Action persistence and
authorization, exact approval/plan binding, execution gateway, Unknown recovery,
crash recovery and prior cleanup matrices all passed. No Run/Action state,
authorization boundary, recovery policy or UI was changed.

## 17. Remaining SQLite Cleanup Risk Register

- A driver that physically refuses `close()` can retain its lock. The code
  attempts close and does not claim release; offline repair/inspection is needed
  if that real environmental condition occurs.
- Existing-store demo provisioning remains exclusive-create/non-overwrite and
  rejects a repeated call before opening SQLite. This protects prior data.
- No reproduced in-scope P0/P1 remains unresolved.

## 18. Focused Repeated Tests

`tests/test_sqlite_cleanup_consolidation.py` contains 25 unique tests and passed
three separate fresh runs: 25/25 each; JUnit durations 5.434s, 5.206s and
5.517s. Runs were kept separate and not added to the unique total. Neighboring
Run/Action/approval/recovery/migration tests also passed in a 546-test run; the
subsequent complete suite provides the final current-tree regression result.

## 19. Full Regression Results

Previous baseline: 1,153 deterministic passed, 5 live deselected. New unique
tests: 25. Current complete suite: **1,178 passed; 0 failed, 0 errors,
0 skipped; 5 live deselected; exit code 0**. Pytest/JUnit duration was
995.02s / 994.884s; parent wall duration was 1,003.025s.

## 20. Browser Compatibility

The full run included all 126 existing browser tests. An independent browser
subset also passed **126/126**, with the two live-browser cases deselected by
`browser and not live`. Run/Approval and Action UI, capability masking/clearing,
admission/authorization/execution separation, Unknown no-replay, reload and
response-loss behavior remained green. Five browser/driver contexts were closed;
browser credential artifact scan passed (50 in-memory managed credentials,
zero exposed). No static UI file changed.

## 21. Packaging and Security

Current source passed `ruff check .`, `ruff format --check .`, `git diff --check`,
`uv lock --check` and `uv tree --locked`. Final offline wheel and sdist rebuilds
after this report was added passed. The wheel contains 53 members; the sdist
contains 184 members, including this report, its JSON evidence and the new
regression module. The noneditable wheel was reinstalled
into the isolated Python 3.13.9 environment under `D:\Ollama\Temp` using the
hash-locked offline export (22 runtime dependencies); `uv pip check` and the
final `tests/package_check.py` run passed from outside the checkout. The probe
verified byte-identical runtime/schema resources and controlled HTTP
Run/Action/execution/replay behavior; it is not browser or live-LLM evidence.

The working source inventory is 45 runtime `.py`/`.html` resources and four
schema JSON resources. High-signal secret scan found zero matching files; no
tracked or nonignored SQLite/DB/WAL/SHM/private-key artifacts were found. Final
wheel/sdist scans found zero prohibited database, key or runtime members. Test
process report: 71/71 owned children stopped, readers joined and listeners
closed; 63 unique recorded ports had zero current listener collisions. The
isolated package environment and ignored `.runtime` evidence are retained for
review, not claimed deleted.

## 22. P0/P1/P2 Assessment

- P0: none.
- P1: the two reported initialization defects and the three additional dynamic
  cleanup defects were reproduced and corrected. No unresolved reproduced P1;
  full regression, browser and package gates passed.
- P2: physical release cannot be guaranteed when a deliberately faulty driver
  refuses close; surfaced accurately as described above. Safe repeated
  provisioning refuses rather than overwrite an existing private store.

## 23. Consolidation Final Gate

- `DAY3_SQLITE_CLEANUP_CONSOLIDATION=PASS`
- `DAY3_SQLITE_CLEANUP_DYNAMIC_AUDIT=PASS`
- `DAY3_SQLITE_CLEANUP_REGRESSION=PASS`
- `DAY3_FINAL_FREEZE=NOT_RERUN`

All changes remain uncommitted for independent review.

## 24. Requirements Before Next Final Freeze

Review the four production cleanup diffs and focused evidence; retain the
migration correction and historical FAIL records unchanged. Then perform a
separately authorized Final Freeze attempt against the reviewed worktree. This
consolidation itself is not freeze/release authorization.
