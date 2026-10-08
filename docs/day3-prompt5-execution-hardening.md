# Day 3 Prompt 5 — Execution reliability hardening

Final gate: `DAY3_PROMPT5_EXECUTION_HARDENING=PASS`. Complete regression passed 1089
unique deterministic cases, zero failures/errors/skips and five live deselected. P0 = 0; P1 = 0.
This is local reliability evidence, not production exactly-once or power-loss qualification.

## 1. Scope and baseline

Started clean on `main`, with HEAD, origin/main and live GitHub main equal to
`b069ee181e75de80c61a5318148da4da37e97207` (committed/pushed Prompt 4).
Prompt 4 evidence records PASS: 1051 unique deterministic tests, zero failures/errors,
five live deselected and 126 browser cases. The peeled `day2-final-freeze` target remains
`c7d3e1ee051af40c69ec33d888ecf16475a2d789`. No other repository session or unrelated
changes were found. No commit, push, tag or release is authorized by this prompt.

Authority: [Day 3 freeze](day3-action-execution-architecture-freeze.md),
[ADR-013](adr/ADR-013-action-ledger-execution-boundary.md),
[ADR-014](adr/ADR-014-approval-plan-binding.md), [plan](day3-implementation-plan.md),
and Prompt 2–4 implementation/evidence documents. [Structured evidence](evidence/day3-prompt5-execution-hardening.json)
will record the final measured gate; no earlier subset is substituted for full regression.

## 2. Frozen execution invariants

Run approval is not authorization or execution. Exact BOUND_V1 Plan approval, trusted
compiler, current registry binding and freshly verified local capability remain mandatory.
Only one reviewed append_service_note/1 SIDE_EFFECT operation is executable.
Action states remain Pending, Authorized, Executing, Succeeded, Failed and Unknown.
Unknown is terminal for this MVP. No retry, takeover by age, reconciliation or new effect key.

The nine-table SQLite v2 DDL/migration, `user_version=2`, four JSON schemas, dependency
lock and authority documents are unchanged. Claim and result are separate short CAS
transactions; dispatch begins only after a confirmed claim commit, outside the gate transaction.
One attempt, paired event/version and scoped command receipts retain exact spec/grant linkage.
The downstream note and keyed receipt are atomic in a separate SQLite file.

## 3. Fault-injection methodology

`tests/execution_fault_worker.py` is a test-only process, not a product entrypoint or fault API.
It uses the actual repository, compiler, verifier/policy, gateway, registry, DemoNoteExecutor,
Run/Action recovery functions, HTTP handlers and Uvicorn on an ephemeral loopback socket.
Composition follows the same owner-lock/recover-before-serving sequence; it does not pretend
to test private capability-file provisioning, already covered by the existing composition tests.

Authority travels only through a private stdin pipe. No token appears in argv, environment,
test telemetry or disk. Stdout is bounded safe JSON phase/PID/nonce data; access logging,
traces and videos are disabled. Connection proxies delegate real SQLite operations and
insert barriers immediately before/after actual COMMIT, never fabricate a note or receipt.
A separate fsynced test journal counts entered adapter calls, not business outcomes.

Parent/child pipe barriers, asyncio Events and a 20-caller thread barrier select the windows.
No random scheduling, inferred age or unbounded sleep selects a crash. All injected resources
are external isolated synthetic files. Full HTTP is used to observe actual lost responses and
concurrent commands; downstream port-only children need no HTTP server.

## 4. Real process termination tests

Owned `Popen` handles directly launch the base CPython interpreter with the dev site-packages
on PYTHONPATH. On Windows the venv python.exe launcher can report a different PID from the
actual owner; an initial harness assertion caught this and the launcher is not used as kill proof.
Each kill checks the actual child PID, random nonce, observed phase, live retained handle and
resolved external storage. Windows Popen.kill uses TerminateProcess; no PID search or broad kill.

Killed children are waited for; parent pipe readers are joined and streams/clients are closed.
Normal teardown requests stop and uses a bounded owned-handle kill fallback if needed; it is
not misreported as graceful shutdown. Every HTTP port is probed closed after teardown.
Direct downstream children may also be killed after their confirmed receipt event, proving
receipt persistence across actual interpreter death. The product adapter spawns no helper.

## 5. Crash-window matrix

The following values were checked against independent connections and persisted rows;
calls/notes/receipts are per isolated Action, not aggregated suite totals.

| Window / actual barrier | Durable before death | After stopped-owner recovery | Calls | Notes / receipts | Attempts / final events |
| --- | --- | --- | ---: | ---: | ---: |
| A: gate claim before COMMIT | Authorized/v2 | Authorized/v2 | 0 | 0 / 0 | 0 / 2 |
| B: gate claim after COMMIT, before permit return | Executing/v3 | Unknown/v4 | 0 | 0 / 0 | 1 / 4 |
| C: adapter entered, before downstream operation | Executing/v3 | Unknown/v4 | 1 | 0 / 0 | 1 / 4 |
| D: actual downstream COMMIT, before adapter result | Executing/v3 | Unknown/v4 | 1 | 1 / 1 | 1 / 4 |
| E: actual identity-rejection no-effect result, before result return | Executing/v3 | Unknown/v4 | 1 | 0 / 0 | 1 / 4 |
| F: gate Succeeded COMMIT, before HTTP delivery | Succeeded/v4 | Succeeded/v4 | 1 | 1 / 1 | 1 / 4 |
| G: lost downstream acknowledgement; gate Unknown COMMIT, before HTTP delivery | Unknown/v4 | Unknown/v4 | 1 | 1 / 1 | 1 / 4 |
| H: admission or authorization before COMMIT | No newly committed command | Exact pre-command rows | 0 | 0 / 0 | No orphan Action/grant/event/receipt |

Each killed request delivered no HTTP response. H includes both admission and authorization,
not just one selected edge. E deliberately does not reconstruct a Failed outcome from an
ephemeral no-effect result that never reached a durable gate result transaction.

## 6. Single-owner and recovery safety

An active async adapter barrier holds the real OS owner lock while a second process starts.
The competitor receives startup denial before recovery/serving; existing Executing rows stay
unchanged. Killing the retained active owner releases the lock; only
then does the restarted owner recover Executing to Unknown. An existing .owner file alone
does not block restart. Simultaneous starts select one real owner and one denied process.

Every restart explicitly attests the predecessor/helpers stopped and holds the frozen lock.
The flag is not a universal process detector or permission for an arbitrary recovery caller.
The controlled adapter creates no detached helper, so killing the owned process establishes
its quiescence. No timeout, stale timestamp, GET, client disconnect or receipt lookup transfers ownership.

Recovery repetitions 1, 2 and 10 plus a second actual interpreter restart converge without
additional events/versions/grants/attempts/effects or dispatch. Approved/v5 Run, exact Plan,
approval and binding remain unchanged. A separate interrupted PlanGenerating Run is rejected
by Run recovery while Action recovery independently records Unknown. Existing six-state,
corruption/schema and forbidden-provider recovery tests remain part of full regression.

## 7. CAS and concurrency

Two 20-request real HTTP cases use identical and independent keys for one Authorized/v2 Action.
Actual per-command SQLite connections perform short claims; a test-only drained-claims Event
lets all contenders finish before the independent write-lock probe. This avoids mistaking a
different legitimate claim transaction for a transaction held across adapter work.

Each case checks one non-replayed winning command, one attempt, one adapter entry, one note,
one receipt and four events. Same-key contenders replay with 200/202; different-key losers
receive 409. Stale/current versions with fresh keys cannot start another attempt. Existing
20-thread/independent-connection claim tests and two-tab browser CAS cases are preserved.

## 8. Admission/authorization/execution idempotency

Actual post-COMMIT/pre-response deaths test admission and authorization. Two independent
interpreter restarts replay the exact accepted body/key and retain identities and business rows;
changed bodies under the same key return 409. Execute F/G/B–E likewise replays only the
current durable snapshot after restart; different keys cannot dispatch again. The final test
revision also explicitly sends an altered execute body with the retained key after restart.

Command receipt acceptance confirms admission/grant/claim, never invents confirmed external
success. A/v2 retains no execute receipt or attempt because its claim rolled back. No effect-key
regeneration, semantic Intent deduplication, model replanning or automatic retry is introduced.

## 9. Downstream effect and receipt evidence

The reviewed adapter is used against an actual independent provisioned SQLite store.
Same key/payload returns the retained UUID receipt; different payload gives confirmed
PAYLOAD_CONFLICT only after a successful lookup. Twenty independent connection contenders
retain at most one note/receipt. Frozen 250ms busy contention may return Unknown, not a false
payload rejection or permission to retry; confirmed same-payload results match the original receipt.

Two separate provider-port processes are released together, then an actual reopened interpreter
returns the same receipt. Port-only tests do not authorize a gateway retry or mutate the Action.
The D crash demonstrates a real committed note/receipt surviving gate-owner death even though
Action recovery cannot infer success. Schema/integrity/FK checks and gate/downstream separation
are independently checked, without distributed or production exactly-once claims.

## 10. Unknown outcome semantics

Timeout/cancellation after a real effect, lost downstream COMMIT acknowledgement, arbitrary
post-effect adapter exception and interrupted dispatch yield durable Unknown when the gate
result write succeeds. A dispatch interruption with zero observed note is still not converted
to Failed. Unknown survives restart and exact-key read replay; new Execute cannot invoke again.
No reconciliation, background work, provider status lookup or duplicate terminal event occurs.
Confirmed no-effect rejection alone may produce Failed; positive matching receipt evidence
alone may produce Succeeded. Existing malformed outcome/tamper/security tests preserve this distinction.

## 11. Result persistence failure behavior

Real gate write-lock contention and controlled result UPDATE/COMMIT/connection interruption
return a safe unavailable-store error without an Action success payload. Before a terminal
commit, Executing/v3 and three events remain; after stopped-owner restart, Unknown/v4 and
one final event appear. A lost acknowledgement after an actual Succeeded commit returns an
error but GET/replay observes Succeeded/v4. Every case retains one call/note/receipt and no retry.

Wrong attempts and late/duplicate completions after process recovery cannot overwrite Unknown
or append events. Existing stale terminal completion and result atomicity tests are preserved.
The single production fix normalizes cleanup failures and attempts close even when rollback
fails; it does not swallow a fault as a successful commit or assume durable Unknown.

## 12. Timeout/cancellation behavior

DemoNoteExecutor remains an async interface containing synchronous SQLite work. A real
downstream BEGIN IMMEDIATE blocker plus a 10ms tool budget stalls the event-loop heartbeat
well beyond that budget before the conservative Unknown/no-effect result. Exact measured
durations are retained in per-run JSONL and the final evidence. asyncio.timeout is cooperative,
not preemption of blocking SQLite/disk/OS work. Frozen 250ms busy handling is not a physical
disk-completion bound; the authority freeze explicitly excludes a hard disk/OS timing guarantee.

Expired context prevents a downstream effect. Async cancellation after a committed note
persists Unknown and re-raises CancelledError; Uvicorn returns a generic 500, not a fabricated
confirmed failure. The parent accepts that transport error and independently reads Unknown.
Real process death during blocking work is covered by the crash barriers. No speculative
thread offload is added: that would require a new helper/quiescence contract outside this prompt.
Soft event-loop responsiveness is an explicit P2 limit, not disguised as universal cancellation.

## 13. Existing UI compatibility

The UI and HTTP contracts are unchanged. Complete regression passed all 126 existing
browser cases on installed headless Edge. These include six real states/reload, Unknown
without Execute, stale versions, lost real response, memory-only capability and inert XSS.
The current five Action screenshots were visually inspected: Pending, Authorized, Executing,
Succeeded and Unknown. Capability inputs are empty, Unknown has a possible-effect warning,
no retry control is offered and Run remains Approved/v5.

## 14. Defects found and fixed

One focused production defect: a closed connection during result commit makes finally's
in_transaction access raise ProgrammingError, masking DATABASE_UNAVAILABLE as INTERNAL_ERROR/500.
The real HTTP `result-close` reproduction initially failed its safe-store error assertion.
`sqlite_actions.SQLiteActionRepository._transaction` now nests cleanup so close is always
attempted after rollback/property failure and translates cleanup exceptions to the fixed
DATABASE_UNAVAILABLE envelope. The same real-process case now checks 503, Executing before
restart and Unknown after restart, with one actual effect and no resend.

Three focused cleanup regressions inject in_transaction, rollback and close faults, verify
the connection is actually closed, unchanged rows and the safe store error. This is safe
error/cleanup hardening, not a claimed prior P0 authorization or duplicate-effect defect.

Resolved harness corrections are not product defects: Windows launcher PID mismatch;
connection-proxy row_factory delegation; cancellation's non-JSON transport error; unnecessary
process-exit timing assumptions after a confirmed downstream result; and conservative Unknown
under genuine downstream lock contention. The final tests do not relax atomicity, CAS,
single-owner, one-attempt, stable receipt or no-retry assertions.

## 15. Complete regression results

38 new unique hardening cases are collected. Three independent focused runs use new explicit
external basetemp roots, with separate JUnit and process/phase telemetry in
`.runtime/day3-prompt5`. Runs 1/2 precede only the additional execute-body-conflict assertion;
run 3 and complete regression include that assertion. All use the same production fix.
All three focused runs passed 38 cases, zero failures/errors, with JUnit durations 316.161,
318.280 and 319.259 seconds (console 316.50, 318.55 and 319.55). Complete regression passed
1089 unique cases, zero failures/errors/skips, five live deselected, in 914.63 seconds
(JUnit 914.486). Its JUnit is `.runtime/day3-prompt5/full.xml`; current browser evidence is
`.runtime/browser-evidence/20261008T130025232019Z/results.json`. Each focused run and the
full run recorded 71 closed child processes, 20 explicit barrier kills and 63 closed HTTP
ports; the 284 total records are scoped to these four successful runs, not development subsets.
Overlapping development subsets and repeated passes are not added to unique counts.

Reproduce with the existing dev interpreter and a never-before-used external basetemp:

```powershell
$env:P5_TEST_PROCESS_REPORT = '<absolute ignored evidence directory>/processes.jsonl'
.venv/Scripts/python.exe -m pytest tests/test_execution_hardening.py -q --basetemp=<new external test root>
.venv/Scripts/python.exe -m pytest -q --tb=short --junitxml=.runtime/day3-prompt5/full.xml --basetemp=<another new external root>
```

Lint/format/diff, offline lock and source/installed dependency checks passed. The rebuilt
wheel's isolated installed probe passed after the production fix: 45 runtime/four schema
resources byte-identical, actual protected HTTP execution/replay and one note/receipt.
The package probe is not browser evidence. Final archive refresh includes final documentation
and evidence only, with no runtime change. Final candidate/archive scan counts are recorded
in the structured evidence after that refresh.

Final scan checked 170 candidate files, wheel 53 files and sdist 171 files, with zero
high-signal secrets or prohibited database/private credential/cache/traversal artifacts.
Each archive retains 45 runtime/four schema files. Fourteen JUnit/child-telemetry artifacts
contain zero nonuniform 64hex authority literals (one uniform synthetic tamper-test value);
50 managed browser credentials were also dynamically checked with zero exposure.
HEAD/tracking/live GitHub main and the Day 2 tag are unchanged. The index is empty;
only the six scoped source/test/document/evidence paths are modified/new and remain uncommitted.

Cleanup checks confirmed all five browser/driver contexts closed, 127 browser-lane HTTP
servers stopped, 50 Action/13 Run business directories removed, 284 recorded child processes
stopped, zero current listeners on 378 distinct recorded ports and zero matching task processes.
Repository runtime DB/WAL/SHM and private capability counts are zero. All four successful
test roots and the isolated package root have zero private capability files. 999 synthetic
DB/WAL/SHM files remain in the four external basetemp roots (76/76/76/771); they are retained,
not deleted. Development/default pytest caches are not included in that scoped count and
were not broadly removed. No user/production database was touched.

## 16. Remaining limitations

Local Windows, cooperating single-owner processes and headless installed Edge only; no
POSIX/cross-browser/physical power-loss/cloud/hostile-host qualification. No full WCAG audit,
production identity/RBAC, HA, durable helper lease, external reconciliation or automatic retry.
Synchronous SQLite has cooperative/soft deadline and responsiveness limitations. These
tests do not infer successful completion from an Unknown row or absence of a note.

Benign synthetic pytest/package caches may remain outside the repository under the frozen
non-blocking rule, with no live owner/listener/credential or user/production database.
They are retained, not claimed physically deleted. No old global caches or user data are removed.

## 17. Deferred Prompt 6 final freeze requirements

Prompt 6 is not started. A separately requested freeze should review source/evidence chronology,
the immutable Day 2 tag and v1 rollback data prerequisite, complete unique regression and current
package identity, all safety boundaries, honest P2 limits and exact staged/delivered scope.
Git delivery/tagging requires separate current authorization. No new tool, retry/resolver,
worker/HA, auth expansion, schema migration, cloud/MCP or Day 4 feature is included here.
