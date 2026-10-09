# Day 3 Prompt 6-R — independent final freeze rerun: FAIL

Ready to freeze: **NO**. The corrected v2 Run transaction passes fresh revalidation,
but a separate migration cleanup P1 blocks this freeze and Day 4 release readiness.
[Structured evidence](evidence/day3-final-freeze-rerun.json) records actual observations.
No product repair, Git staging, commit, push, tag or release was performed.

## 1. Purpose and baseline

Task DAY3_FINAL_FREEZE_RERUN_AFTER_P1_FIX; current review base
3de7802a1266cc48fd94213b71816ad88d292d42 on main.
Initial status clean; index empty; HEAD = origin/main before and after successful
git fetch origin main. Prompt 5, failed-audit and corrective commits are ancestors.
The immutable day2-final-freeze target remains
c7d3e1ee051af40c69ec33d888ecf16475a2d789. Repeated status/diff checks observed no
concurrent repository edits. Other host Codex processes exist; their presence alone
was not represented as evidence of repository modification or absence of all sessions.

## 2. Historical FAIL and corrective chronology

Prompt 5 cfa371cd27ef833bac93edbf8d743f73851f7222 passed its bounded hardening scope.
713deeb63265bc7b4f3427a11e0309433b1d4d6c archived the first failed review.
3de7802a1266cc48fd94213b71816ad88d292d42 committed the narrow v2 Run corrective patch.

The first review remains DAY3_FINAL_FREEZE=FAIL with
D3-FREEZE-P1-V2-RUN-CLEANUP and its original incomplete 451-pass/internal-error history.
The corrective record remains P1 fix/regression PASS and final freeze NOT_RERUN.
These are dated task results, not assertions that those reports remained untracked
after their later archive/checkpoint commits. Neither historical record was rewritten.

## 3. Current Git and byte integrity

The four historical report/evidence files equal their current committed Git blobs.
SHA-256 values, in that order:

- docs/day3-final-freeze.md: 528dd6a65b45d65a4b91792f166a6a47bde107ef5ee0b7c327507da7507b4d98
- docs/evidence/day3-final-freeze.json: 7f1662ca9976668ce2d3ce9f6d362e163153db3a4cd553fdf223bf2536fbbfe5
- docs/day3-p1-v2-run-cleanup-fix.md: 22b50bf8f1f8605f4d922b0b1e64374f86d477660dff74112b73f228d21f7d59
- docs/evidence/day3-p1-v2-run-cleanup-fix.json: cad600abf789e01e4de2a3fe73fa0e67973e1f89dce3dae3a6418a5f1ffd33cf

Four JSON schemas, Run/approval domain and original SQLite v1 DDL module are
byte-identical to Day 2. All other tracked files remain unchanged from starting HEAD.
Only the two new rerun documents are nonignored outputs; index remains empty.

## 4. Frozen architecture and reviewed authority

Reviewed README, Day 3 freeze/plan, ADR-013/014, Prompt 2–5 documents and structured
evidence, failed review, corrective record, relevant domain/application/ports/adapters,
static UI, fixtures, browser/process/fault harness and package probe.
Earlier architecture documents describe their own DOC-ONLY stage; current source and
fresh evidence determine implemented acceptance.

Run: IntentReceived, PlanGenerating, PlanValidated, Ready, AwaitingApproval,
Approved, Rejected, PlanRejected.
Action: Pending, Authorized, Executing, Succeeded, Failed, Unknown.
TURN_REQUIRED=NO. Run + Action, independent versions; Approved != Authorized != Executed.
No automatic admission/authorization/dispatch from approval, GET or page load.
No model-to-tool route, retry, public reconciliation, terminal resurrection or new state.

## 5. Original P1 independent revalidation

Fresh tests/test_v2_run_cleanup.py: **30/30 PASS**, 26.22 console seconds,
26.217 JUnit seconds, 33.176 parent-wall seconds, exit 0.
Ten real SQLite approval fault cases, twelve primary-exception identity cases and
eight actual Uvicorn/HTTP cases. Retained strong connections and a second independent
BEGIN IMMEDIATE / UPDATE / COMMIT demonstrate release without GC rescue.
Twenty-nine second-writer observations in the complete run; maximum 0.005953 s.

Rollback/property failures still attempt close; RunStoreError, BindingError and
CancelledError identity survives ordinary secondary cleanup faults.
Genuine storage faults map to safe DATABASE_UNAVAILABLE/503; stale version remains 409.
Precommit failures reopen waiting/v4 with no decision/binding and four audits;
committed/lost-ack cases remain Approved/v5 with one decision, BOUND_V1 and five audits.
No guessed rollback, false success, retry or orphan record. Original P1 does not reproduce.
Close-ack fixtures release the actual handle before raising; no guarantee that a driver
which refuses physical close can always be recovered.

## 6. Approval-to-Plan security

canonical-plan-v1 and approval-plan-v1 use versioned canonical UTF-8 SHA-256.
Full Plan content and exact nine-key decision context are bound; client digests are
not authority. Both new choices store decision/companion/Run/audit atomically.
Full regression covers canonical equivalence/significant changes, missing/copied/
malformed/context proof, T1–T9, no rebinding, rejected admission and uncertain commit.
Fresh HTTP changed-Plan denial is 409 APPROVAL_PLAN_MISMATCH; genuine migrated v1
Approved denial is 409 LEGACY_APPROVAL_UNBOUND, with zero new Actions/effects.
Healthy legacy Run reads remain compatible but do not prove historical Plan content.

## 7. SQLite v2, migration and rollback

Nine STRICT tables: runs, approval_decisions, audit_events, approval_plan_bindings,
actions, action_authorizations, action_attempts, action_command_receipts, action_events.
Explicit v2 selection; v1 default still rejects v2. No implicit upgrade/downgrade/reset.
Existing complete-suite migration cases pass: WAL-aware private backup, original
rows/DDL preservation, legacy null-proof classification, future/partial schema denial
and ordinary fault rollback. Old-code rollback requires a valid pre-migration v1 backup.

**Additional independent cleanup fault verification FAILS**. This is distinct from the
ordinary migration tests and from the repaired v2 Run helper; details in section 21.
Do not interpret green existing migration tests as proof of every cleanup fault.

## 8. Action ledger and compiler

Server UUID/spec/effect identity, immutable specification and stable Run-step uniqueness;
explicit exact-bound whole single-step admission produces Pending/v1.
Trusted compiler recomputes schema/semantics/risk/proof/spec and denies unsupported
whole Plans, maintenance/read/multistep and missing binding; no partial compilation.
Scoped same-key/same-body receipts return existing durable facts; changed body conflicts.
Run version is unchanged. Reopen/pure reads/retired-binding historical compatibility and
independent CAS/history validation pass; no planning call in Action operations.

## 9. Capability and authorization

Explicit unpredictable 256-bit local capability; no default privileged principal.
Constant-time fingerprint verification; server-owned subject/scope and issued immutable
PrincipalContext, operation allowlist and exact Action/spec/policy linkage.
LOCAL_USER is historical local approval, never execution permission.
Authorize commits grant + Authorized/v2 + event + receipt under CAS and does not execute.
Missing/invalid credentials, insufficient execute permission, tamper and stale commands
are denied before forbidden mutations/effects. Local-demo auth is not production identity/RBAC.

## 10. Execution gateway

Explicit execute only: fresh permission/exact grant/current trusted binding -> short
durable claim/attempt/receipt/event -> transaction ends -> one adapter call -> guarded result.
The positive audit independently observes Executing and one attempt before dispatch,
and an independent gate writer succeeds during adapter work.
One winning claim/attempt, no permit after unconfirmed claim commit, no stale/other
attempt completion overwrite, no second dispatch from terminal/Unknown or receipt replay.
Result-store failure never asserts a durable Unknown without its actual commit.

## 11. Controlled downstream adapter

append_service_note/1, service-demo, exact adapter/binding/scope/arguments only.
No arbitrary path, shell, endpoint or dynamic plugin. Stable effect key and exact payload.
Separate downstream SQLite atomically stores note + opaque keyed receipt, independent of
gate storage; same-key/payload dedup and conflicting payload rejection survive reopen.
Actual port/concurrency/restart cases pass. This is one controlled provider contract,
not universal exactly-once external effects or eventual delivery.

## 12. Crash, recovery, Unknown and soft timeout

Historical Prompt 5 A–H review is supported by current full-suite execution of all 38
hardening cases. Current A–G disk/call counts match history; H covers interrupted
admission and authorization. No separate repeat of the entire expensive matrix was added.

Fresh targeted smoke: **3/3 PASS**, 41.99 JUnit seconds:
B after claim before dispatch -> Unknown, 0 calls/notes, 1 attempt, 4 events;
D after real downstream commit before result -> Unknown, 1 call/note/receipt,
1 attempt, 4 events; active-owner competitor denied before recovery.
B/D repeat recovery 1/2/10 and a second interpreter restart without new versions/events/effects.
Only nonce/PID/phase-verified owned handles are killed; no unrelated process termination.

Succeeded requires matching positive receipt, Failed reliable no-effect evidence,
Unknown uncertainty (including a real committed effect). Unknown is terminal here:
no automatic retry/reconcile/second attempt/age takeover or background dispatch.
Synchronous SQLite can block the asyncio loop: the 10 ms budget is cooperative,
not a hard interrupt. No universal bounded latency, physical cancellation or power-loss claim.
This existing timeout limit is P2, not a reason to downgrade the newly found leak.

## 13. Fresh real-network positive smoke

Real Uvicorn/FastAPI/RunService/v2 control DB and separately provisioned downstream DB,
memory-only generated test authority and deterministic newly bound Approved proposal.
GET Run -> explicit Admit Pending/v1 -> Authorize/v2 -> Execute -> Succeeded/v4;
Run remains Approved/v5. Closed server, newly opened repositories and a second real
server preserve GET/replay result with zero added effect.

Independent disk counts: 1 Action, 1 grant/authorization transition, 1 winning claim,
1 attempt, 1 counted gateway adapter entry, 1 note, 1 downstream receipt,
3 accepted command receipts and 4 Action events. Claim-before-dispatch and outside-
transaction writer probes PASS. No model call, mocked Action response or inferred count.

## 14. Fresh real-network negative smoke

Each case has real HTTP, isolated SQLite and safe expected=actual response.
Every forbidden command leaves business ledger rows unchanged and creates
zero new adapter calls/notes/receipts/attempts. I/J intentionally have one already
committed baseline note/receipt; that is not a new forbidden effect.

| Case | Scenario | Expected and actual HTTP/code | New effects | Result |
| --- | --- | --- | ---: | --- |
| A | Missing capability | 401 AUTHENTICATION_REQUIRED | 0 | PASS |
| B | Invalid capability | 401 AUTHENTICATION_REQUIRED | 0 | PASS |
| C | Insufficient execute permission | 403 ACTION_NOT_AUTHORIZED | 0 | PASS |
| D | Non-Approved admission | 409 RUN_NOT_ACTION_ELIGIBLE | 0 | PASS |
| E | Migrated legacy Approved | 409 LEGACY_APPROVAL_UNBOUND | 0 | PASS |
| F | Plan changed after approval | 409 APPROVAL_PLAN_MISMATCH | 0 | PASS |
| G | Action specification tamper | 503 DATABASE_UNAVAILABLE | 0 | PASS |
| H | Stale Action version | 409 STALE_ACTION_VERSION | 0 | PASS |
| I | Execute Succeeded | 409 EXECUTION_ALREADY_STARTED | 0 | PASS |
| J | Execute Unknown | 409 EXECUTION_ALREADY_STARTED | 0 | PASS |
| K | Retired binding | 409 TOOL_NOT_BOUND | 0 | PASS |
| L | Approval commit + rollback fault after P1 fix | 503 DATABASE_UNAVAILABLE | 0 | PASS |

First independent smoke controller used the wrong SQL column and stopped safely;
its isolated server/DB teardown completed. Corrected only the ignored diagnostic to
validated_plan_json and source-authoritative error spellings, then the entire smoke passed.
No production code, tests or expected product guarantees changed.

## 15. UI, browser and reproducible demo

Full **126/126 browser subset PASS**, not added to 1119. Installed headless Edge.
58 controlled-response frontend cases; 6 actual planning HTTP cases;
13 real Run HTTP/SQLite and 49 real Action HTTP/v2/downstream cases.
Real-backend cases do not fabricate Action responses; lost-delivery tests fetch the real
committed response then discard transport, explicitly not backend mock proof.

Independent Run/Action versions, explicit buttons, page/GET purity, reload/deep links,
same-key locking, races, inert XSS/textContent, six states, safe network uncertainty,
masked/cleared memory-only capability and Unknown no-Execute pass.
Five current full-run captures were visually reviewed; capability fields empty,
Approved/v5 preserved, and Unknown warns possible effect/no replay.

Prompt 4's documented deterministic browser command was reproduced with a fresh
external basetemp: **2 PASS / 47 deselected**, 9.727 JUnit seconds
(47 are the deliberately unselected demo module cases, not full-suite live exclusions).
Actual execution_main --predecessor-stopped was also started hidden with temporary
private provisioned capability/resource, deterministic bound preparation and explicit
real HTTP Admit/Authorize/Execute/GET. One note/receipt and Succeeded observed.
Unused loopback model port 9; no planning request/inference. Owned child waited,
listener closed, private business directory removed. Live Ollama is not mandatory.
This verifies current setup paths; it does not revise README's historical snapshots.

## 16. Complete deterministic regression

Historical corrective floor 1119. Current collect-only: **1124 total / 1119 selected /
5 existing live deselected**. Full actual result: **1119 unique PASS; 0 failed, 0 errors,
0 skipped; exit 0**. Console 969.1 s; JUnit 968.984 s;
parent wall 976.81 s. Browser 126, focused 30, crash 3 and demo 2 are
overlapping subsets/repeated observations, never added to unique total.

Exact invocation from repository, with file-backed output and no permissive exit handling:

```powershell
$env:P5_TEST_PROCESS_REPORT=(Join-Path (Get-Location) '.runtime/day3-final-freeze-rerun/20261009T003459Z/full-processes.jsonl')
$env:PYTHONIOENCODING='utf-8'
.venv/Scripts/python.exe -u -m pytest -q --tb=short --color=no --capture=fd --basetemp=D:/Ollama/Temp/aig-rerun-full-20261009T003459Z --junitxml=.runtime/day3-final-freeze-rerun/20261009T003459Z/full.xml *> .runtime/day3-final-freeze-rerun/20261009T003459Z/full.log
$testResult=$LASTEXITCODE
exit $testResult
```

Original pytest Errno 22 terminal/internal error did not recur. Green regression does
not cover the additional migration-cleanup scenario and cannot override that P1.
The already-running independent suite completed safely after detection because the
fault fixture was explicitly closed/removed and did not affect its isolated databases.
No further feature implementation or repair was started.

## 17. Packaging and isolated installation

Offline uv wheel/sdist builds PASS; lock check and 34-package source dependency check PASS.
Fresh noneditable wheel install outside checkout with locked/hash-verified requirements:
22 runtime dependencies + package, installed dependency check PASS.
Existing package_check confirms import from isolated site-packages, not source;
45 runtime and 4 schema resources byte-identical, static UI present, default v1 hidden
and separate protected execution/binding/replay/reopen path PASS.
Its transport is controlled ASGI HTTP, not browser/network evidence; those have separate proof.
Final archives are refreshed after documentation only: wheel 53 files,
sdist 178 files, same 49 resource bytes and no prohibited members/links.
Archive hashes remain in ignored audit.json to avoid self-referential sdist/evidence hashes.
This is clean runtime installation, not an offline dev-environment/browser installation guarantee.

## 18. Scoped security and cleanup

177 tracked/nonignored candidate files scanned, no high-signal keys/private
credentials/config/DB/WAL/SHM/cache/model binary/path violations; public .env.example
inspected and contains local-only documented values, not a private token.
Archive and retained runner/child-artifact pattern scans PASS; no usable authority literals.
Managed browser credentials checked in memory against artifacts/JUnit: 50,
zero exposure. No browser traces/videos. Pattern checks are scoped hygiene, not exhaustive audit.

80 Action owned-child records closed, waited, readers joined and listeners closed;
127 full-browser HTTP servers stopped; 5 browser/driver contexts closed.
Additional focused HTTP, targeted smoke, documented browser and CLI resources also closed.
229 distinct recorded ports rechecked, zero listeners. Final task-process check zero;
repository .runtime business DB/WAL/SHM zero; no tracked/staged secret or generated database.
Migration diagnostic handles were explicitly rolled back/closed after measuring the product leak.
Synthetic external pytest datasets and isolated package environment remain retained, not deleted;
exact scoped database counts are in JSON. No user database, old global cache or Ollama touched.

## 19. Day 4 competition-adapter readiness

CORE_REUSABLE=YES at the existing reviewed local architecture boundary.
READY_FOR_DAY4=NO while the new mandatory storage-cleanup P1 is unresolved.

| Question | Assessment |
| --- | --- |
| A: unchanged Run states | Yes; adapters belong behind separate Action/ToolExecutor. |
| B: existing registry registration | Trusted explicit registration pattern reusable; current constructor only accepts DemoNoteExecutor, not arbitrary plugins. New types need reviewed registry/compiler/policy allowlist work. |
| C: model-independent validation | Yes; server approval/validator/compiler/spec checks remain authoritative. |
| D: reusable capability/auth | Ports/verified-principal pattern reusable; LocalDemoAuthorization itself is demo-specific, not a production policy. |
| E: durable CAS | Yes; one durable winning claim/attempt before dispatch. |
| F: downstream idempotency | Clear stable scope/key/payload/retention/receipt contract; each provider must substantiate its own guarantee. |
| G: outcomes | Existing three outcome categories and six Action states suffice for a compatible adapter. Current receipt/no-effect evidence formats are constrained and require mapping review. |
| H: non-replaying recovery | Yes; abandoned claims become Unknown, no model/tool/status lookup. |
| I: real controlled demo | Yes; fresh network, browser, CLI and isolated package proof. |
| J: core architecture changes | No Run/Action lifecycle redesign needed for a compatible single-step SIDE_EFFECT feature. Current hardcoded ToolBinding/compiler/retained-spec/registry/policy validations are not configuration-only extensibility and need narrowly reviewed implementation changes. |
| K: interfaces | Listed below; retained identities/history cannot be silently reinterpreted. |
| L: actual blockers | New migration cleanup P1 blocks freeze/Day 4 checkpoint readiness. Production/distinct-consent/HA/multistep needs become blockers only if a future chosen integration requires those excluded guarantees. |

Frozen interfaces: ModelProvider / PlanningService / RunService / RunRepository;
ApprovalBinding canonical-plan-v1 / approval-plan-v1; CompiledSpec / ActionCompiler /
ToolBinding; AdmissionCommand / ActionCommand / ExecutionRepository; PrincipalContext /
AuthorizationPort; ExecutionSpec / AttemptContext / ToolOutcome / ToolExecutor /
ToolRegistry; ActionExecutionGateway; recover_runs / recover_actions; static UI + five
protected Action routes, stable body/key/version/result/error contracts.

Day 4 required work after a separate correction/review: select actual target/argument
schema, trusted binding/retained-validation and least-privilege policy; SIDE_EFFECT risk
review; real provider scope/idempotency/receipt retention; bounded sanitized evidence;
timeout/cancellation/Unknown/quiescence; no hidden retries; fault/concurrency/restart/
browser tests. No Qloo/GOSIM/PayPal/cloud/MCP integration is approved or implemented.
CORE_REUSABLE is not READY_WITHOUT_CODE_CHANGES or production readiness.

## 20. Accepted P2 limits

Cooperative synchronous SQLite timeout/heartbeat limits; no hard cancellation or universal
latency; single cooperating owner and local capability, no production identity/RBAC/
authenticated distinct consent/HA/distributed worker coordination; no universal exactly-once,
automatic retry/reconciliation/Unknown resolver, multistep execution, physical power-loss,
POSIX/cross-browser/cloud/MCP qualification or implemented competition adapter.
Benign synthetic external test/install caches retained outside repository.
These do not excuse a leaked migration writer.

## 21. Findings and exact new P1 reproduction

P0 observed: **0**. Unresolved P1: **1**,
D3-FREEZE-RERUN-P1-MIGRATION-CLEANUP.
Source: src/agentic_intent_gate/adapters/sqlite_v2_migration.py, migrate_v1_to_v2,
finally lines 99–104. SHA-256
7c02842d671cb137f877d1055f0ca5619e06181439b631b1762fbbf9aeec4a66.
The main handler catches RunStoreError only (lines 121–125); this diagnostic invokes
the actual library migration, not an HTTP migration route (there is no such route).

Minimal isolation/reproduction: fresh temporary v1 WAL database with one Approved/v5
Run; new private v1 backup; actual migration with owners_stopped=True.
Replace only the migration's first open_existing connection by a real sqlite3.Connection
subclass. Its execute raises OperationalError at CREATE TABLE actions, after the
approval_plan_bindings DDL has executed inside the actual BEGIN IMMEDIATE transaction.
Its rollback also raises before delegating. Retain a strong reference and count close.
Other backup/read connections are unchanged.

Invoke migrate_v1_to_v2; capture escaping type. Before controller cleanup, probe an
independent connection with timeout=0.05 and BEGIN IMMEDIATE.
Then explicitly use sqlite3.Connection.rollback(connection) and .close(connection),
reopen and compare original rows/schema and validate backup. No GC-dependent release.

Two fresh independent runs reproduced exactly:
OperationalError escaped (expected safe RunStoreError/DATABASE_UNAVAILABLE),
rollback_attempts=1, close_attempts=0, still_in_transaction=true;
independent writer blocked before controller cleanup, succeeds afterward.
After **diagnostic controller** cleanup: user_version=1, original three tables/rows
preserved, integrity=ok, private v1 backup valid, no durable orphan rows.
No model/tool/HTTP call or external effect. Both temporary business directories removed.
This is availability/error-contract/connection cleanup P1, not observed P0 data-loss,
approval bypass or duplicate effect; ordinary atomic rollback success does not cover it.

Reproduce retained diagnostic:
.venv/Scripts/python.exe .runtime/day3-final-freeze-rerun/20261009T003459Z/migration_cleanup.py
Safe measurements: migration-cleanup.json, migration-cleanup.log,
migration-cleanup-repeat.log in the same ignored directory.
No production repair or new regression test was allowed/performed.
A separate corrective patch must protect per-acquired-handle close attempts and primary
safe error precedence, then prove real second-writer release/backup/rollback behavior.

## 22. Scope and final rerun gate

Only docs/day3-final-freeze-rerun.md and
docs/evidence/day3-final-freeze-rerun.json created as nonignored deliverables, uncommitted.
Ignored audit scripts/logs/JUnit/packages and external isolated test roots are verification
artifacts, not additional product changes. Historical FAIL/corrective files untouched.
No source/test/schema/dependency/UI/README edits, retry/reconciliation/new adapter,
live Ollama/cloud/MCP, stage/commit/push/tag/release or Day 4 implementation.

DAY3_FINAL_FREEZE_RERUN=FAIL
DAY3_COMPETITION_ADAPTER_READINESS=FAIL
DAY3_FINAL_FREEZE_READY_FOR_CHECKPOINT=FAIL

STOP. A separately authorized migration-cleanup corrective task and independent rerun
are required before any final-freeze checkpoint or Day 4 readiness claim.
