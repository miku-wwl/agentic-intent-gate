# Day 3 Prompt 4 — Action UI and real-backend browser E2E

Final gate: `DAY3_PROMPT4_ACTION_UI_BROWSER_E2E=PASS`. Complete regression passed 1051 unique deterministic cases, zero failures/errors/skips and five live deselected, including all 77 existing and 49 new Action browser cases. [Machine-readable evidence](evidence/day3-prompt4-action-ui.json) records the same scoped results. No commit, push, tag or release was performed.

## Baseline and preserved authority

Started clean on synchronized `main` at `9dd68dc060236e5d96d4ae60acb98fab6a7cbc5d`; local HEAD, origin/main and live GitHub main matched. The stable `day2-final-freeze` tag is `c7d3e1ee051af40c69ec33d888ecf16475a2d789`. Prompt 3's committed deterministic baseline is 1002 passed, zero failures/errors, five live deselected, including 77 existing browser cases.

Authority remains the [Day 3 freeze](day3-action-execution-architecture-freeze.md), [ADR-013](adr/ADR-013-action-ledger-execution-boundary.md), [ADR-014](adr/ADR-014-approval-plan-binding.md) and [implementation plan](day3-implementation-plan.md). No Action/Run state, SQLite schema/migration, approval binding, compiler, capability verifier, authorization policy, claim/idempotency/recovery semantics, executor port, downstream adapter, dependencies or JSON schema changed. A backend defect would stop its affected workflow; none was identified during this implementation.

## One existing UI, two deliberate compositions

The lightweight packaged `adapters/static/index.html` remains the only page: Run, validated Plan, trusted risk and local approval above; legacy stateless planning preview below. The Action section lives within this existing Run console. There is no SPA, frontend dependency, second service or browser-local authoritative ledger.

Default `aig-plans`/v1 retains its existing behavior, hides the new section and still returns 404 for Action routes. The explicitly selected Prompt 3 v2 execution composition enables the section. Run approval, Action admission, authorization and execution remain separate commands. Run stays Approved/v5 while its Action advances independently.

The only backend edit is root-HTML wiring in `local_http.create_app`: when a gateway is configured, the page receives a public, allowlisted operation hint from the configured `CapabilityVerifier`. This supports the frozen single-principal local demo, not permission discovery or identity. No token, principal, scope UUID, credential fingerprint or new API field is embedded. Unknown verifier implementations expose no available operations. Hints become actionable only after a real protected GET succeeds; backend verifier/policy checks remain authoritative on every request. Read-only/missing-operation controls are hidden or disabled. Runtime configuration is assumed stable; hot policy/credential changes and multi-principal discovery are not introduced.

## Capability and browser security

An explicitly labelled password field accepts the existing privately provisioned local capability. Entry copies it into the page's closure, immediately empties the input and verifies visibility through the protected Action list/detail GET. No default credential, login flow, privileged LOCAL_USER inference or body actor/role exists. Clear capability, page navigation and reload discard it. Re-entry is necessary after reload.

No capability is written to localStorage, sessionStorage, IndexedDB, cookies, URL/history, logs, HTML attributes, exported evidence, screenshot or trace. Browser requests use same-origin validated relative UUID paths, `mode: same-origin`, `credentials: omit`, `redirect: error` and `cache: no-store`. Capability is sent only in Authorization headers to the same application's Action APIs. This remains an HTTP loopback/local-host demo, not a production secret manager, identity system or TLS deployment. Browser/host administrators, extensions and a malicious same-origin script are outside this guarantee; JavaScript memory cannot promise forensic secure erasure.

Plan, arguments, notes, result and errors are rendered using `textContent`/DOM node creation, never HTML interpretation. Errors use a bounded frozen-code whitelist and fixed UI text, never raw backend diagnostic messages. The test harness generates unpredictable transient credentials, omits them from fixture repr and suppresses credential-bearing call diagnostics. Access logging, traces and videos are disabled. Captures are made only after the field is empty; a memory-only token scan checks the retained browser artifacts and final JUnit.

## Commands, versions and uncertainty

Admit Action sends only `expected_run_version`; Authorize/Execute send only `expected_action_version`. Each explicit click creates one UUIDv4 Idempotency-Key and retains its exact body/key in memory. Controls synchronously lock while the request is in flight and the same operation/resource cannot be resent from that page. The UI never invents another key to retry, automatically replays a mutation, compiles an Action or performs semantic Intent deduplication. Server CAS, receipt identity and the one-attempt budget still enforce the true boundary across tabs/reloads.

After a successful command or a safe conflict/transport error, bounded GET list/detail refresh reconstructs persisted state. POST response acceptance is not presented as a locally invented success. An execution request additionally schedules one GET after 200 ms to expose the durable Executing claim; there is no continuous polling or request on every render. Manual Refresh Actions remains a read. GET/POST client budgets are 5/12 seconds; frozen server/tool budgets are unchanged. Request sequence/generation fencing discards obsolete reads and rejects an older Action version.

Admission is offered for an Approved/v5, single-step append_service_note/1 SIDE_EFFECT Plan targeting service-demo, with a verified capability and configured admit operation. The frozen Run GET does not expose approval-binding status: the UI cannot infer exact-bound authority from Approved alone. A legacy Approved proposal can therefore reach the explicit command and receives the real 409 LEGACY_APPROVAL_UNBOUND, with a safe hint to create and review a distinct new Run. Backend approval/spec/current-binding checks always decide admission. Unsupported maintenance or multi-step Plans are not offered and actual forced commands are denied as a whole.

| Backend state | Available next command | Truth presented |
| --- | --- | --- |
| Pending | Authorize, with permission | Compiled/persisted; not authorized or executed |
| Authorized | Execute, with permission | Grant recorded; execution still separate |
| Executing | Read refresh only | Claim exists; external outcome unconfirmed |
| Succeeded | Read only | Backend persisted confirmed receipt |
| Failed | Read only | Backend confirmed the intended effect did not occur |
| Unknown | Read only | Effect may have completed; automatic replay disabled |

Unknown is visually prominent and is neither Failed nor retryable. HTTP/network errors are separate from Action state. If the execution response is lost after Succeeded commits, GET recovers Succeeded without another Execute. If GET also fails, the last confirmed snapshot remains visible, a network warning remains and the Execute lock stays set. A result-store fault after a real effect can leave Executing with a database warning; no success, no-effect claim or second attempt is fabricated.

## Reload, deep links and accessibility

`?run_id=<canonical-uuid>&action_id=<canonical-uuid>` contains lookup handles only. Reload fetches the Run, requires capability re-entry, then fetches associated Actions and the selected detail. Action/run association, UUIDs, response shape and monotonic version are checked. Unknown/missing/cross-run Action links fail safely without mutations. Invalid syntax never becomes a fetch path. Creating a distinct Run clears old selection and uses a distinct admission lock; it is a new intentional proposal, not an old Action retry.

Labels, keyboard focus, visible focus outlines, status/alert roles, explicit disabled states and wrapping UUID/argument text preserve the existing accessibility approach. Real browser tests exercise 375 px and 1000 px widths with no horizontal overflow. This is scoped keyboard/DOM/layout verification, not a certified full WCAG or assistive-technology audit.

## Actual browser lane and observed effects

`tests/browser/test_action_ui_e2e.py` contains 49 new deterministic cases. Each browser uses installed headless Edge, actual HTTP on an ephemeral loopback port, actual SQLite v2, real verifier/policy/RunService/compiler/ActionExecutionGateway, and the actual isolated DemoNoteExecutor. Authoritative proposals are prepared with a controlled planning provider and real approval methods, not forged backend success. No live Ollama/cloud call occurs.

Coverage includes explicit happy path and visible claim; six states and reload; no/invalid/LOCAL_USER/admin/read-only/missing-operation/unconfigured credentials; Ready/AwaitingApproval/Rejected/legacy/tampered/unsupported approvals; three duplicate/in-flight commands; two same-version concurrent-tab CAS races; real Unknown after a committed effect; both transport-loss variants; inert script/event/entity/closing-tag/Unicode/newline/long notes; fixed safe results; store failure after an effect; clear/navigation/deep links; keyboard/mobile layout; and distinct new-Run admission.

Only the two response-loss cases intercept browser transport: `route.fetch` sends the real execution request and verifies its real committed Succeeded response before `route.abort` discards delivery. One variant also aborts the subsequent detail GET. No fabricated Action HTTP response, fake note, fake receipt or frontend success substitute is used. Other cases use no Action HTTP interception. Controlled server-side barriers/faults are labelled and preserve backend contracts.

Positive happy path independently verifies one Action, grant and attempt, one adapter call, one downstream note and receipt, matching displayed receipt, unchanged Approved/v5 Run and durable reload with no extra POST/effect. Each negative authorization/approval fixture has zero forbidden transition/call/effect. Same-version races have one 200 winner and one 409 stale response, one grant/attempt and at most one effect. Unknown fixtures deliberately commit one real note/receipt before returning uncertain evidence; that is possible completion, not a duplicate or fake success. Confirmed-failure fixtures have one call and zero note/receipt. Counts are per isolated fixture, not an assertion that the entire suite creates only one note.

Frozen safe result messages are fixed literals: a malicious-but-valid result message cannot be produced by the current backend contract. The real browser test instead supplies an untrusted malicious adapter reason after a real effect; the real gateway rejects it as no-effect proof, persists Unknown and exposes only the fixed safe result. All result rendering also uses textContent. No fake Action response is used to manufacture an impossible contract value.

Development corrections are retained honestly: early fixtures conflicted with Playwright's running event loop and were moved to an owned bounded worker; hidden Action controls were made explicitly disabled; expanded tests corrected a SQL column name, note tuple index and label/frozen-message expectations. These are resolved UI/test-harness issues, not backend semantics changes. The final new-Action-only run passed 49/49 in 89.01 seconds; `.runtime/day3-prompt4/action-stable.xml` records it. Subsets/repeats are not added to the final unique regression count.

## Reproducible 2–3 minute demonstration

The base demonstration requires no model service, permanent capability or user database. From the repository's existing dev environment:

```powershell
.venv/Scripts/python.exe -m pytest tests/browser/test_action_ui_e2e.py -q -k "real_happy_path or unknown_after_real_effect" --basetemp=D:/Ollama/Temp/aig-p4-demo
```

This reproducible controlled browser walkthrough provisions fresh private synthetic stores/credentials, prepares a newly exact-bound Approved Plan, opens the real page, enters the transient credential, explicitly admits Pending, authorizes, captures Executing, executes to Succeeded, independently checks one note/receipt, reloads and verifies no replay. Its second case demonstrates actual Unknown with possible completion and no Execute control. It closes browser/server and removes owned business directories. Capture location is the new `.runtime/browser-evidence/<UTC>/results.json` with five safe images: pending, authorized, executing, happy-path/reloaded Succeeded, Unknown.

For a 2–3 minute presentation, show the recorded Approved Plan (20 seconds), Pending after admission (20), Authorized after its separate command (20), Executing claim then receipt (40), reloaded Succeeded without a new effect (20), and Unknown's possible-effect/no-replay warning (30). These are actual browser captures, not UI mockups. Repeat the test to regenerate evidence. An unattended interactive demo server is not started or left behind by this task. For an operator-presented manual browser session, use Prompt 3's explicit private provisioning and single-owner v2 entrypoint; its server-side planning settings remain required, but live inference is optional and not required to inspect prepared Runs.

## Verification, limitations and scope

Complete deterministic regression passed 1051 unique cases, zero failures/errors/skips and five live deselected in 595.45 seconds (JUnit 595.378 seconds), with all 126 browser cases passing. Command: `.venv/Scripts/python.exe -m pytest -q --tb=short --junitxml=.runtime/day3-prompt4/full-stable.xml --basetemp=D:/Ollama/Temp/aig-day3-prompt4-full-stable`. Final browser evidence: `.runtime/browser-evidence/20261008T101429119842Z/results.json`; pytest exit zero, installed headless Edge 154.0.4258.53. Its 50 isolated Action environments recorded 18 adapter calls, 16 notes and 16 receipts; maximum one note per environment. These include prepared terminal-state fixtures; two confirmed-failure calls created no note. The per-positive-Action guarantee is one effect, not one aggregated suite effect.

Ruff check/format, git diff check, offline lock validation and source/installed dependency checks passed. Offline wheel/sdist build, isolated install outside source and installed HTTP/resource verification passed. The package probe verifies 45 runtime and four schema resources against source bytes, the default hidden composition, enabled masked Action controls/public operation wiring, no embedded credential, and actual protected execution/replay with one note/receipt. The package probe is HTTP/resource evidence, not browser evidence. Its JSON is `.runtime/day3-prompt4/package-verification.json`. Final archives are refreshed after documentation/evidence-only edits; runtime bytes do not change. Wheel has 53 members and sdist 166; no prohibited database/private credential/cache/traversal member or high-signal secret hit. Candidate scan checked 165 files with zero high-signal secret hits; 50 managed Action tokens were independently checked in memory against the full-run browser artifacts/JUnit, with zero exposure. No trace/video was captured.

Cleanup verified 127 HTTP servers stopped and ports released, five browser/driver contexts closed, all 50 Action and 13 Run owned business directories removed, zero currently listening recorded ports or task-owned processes, zero remaining private capability files, zero repository runtime DB/WAL/SHM and zero tracked prohibited artifacts. Across ten exact task-owned external roots, 717 synthetic DB/WAL/SHM cache files and the isolated installed environment remain recoverably retained, not deleted, under the frozen non-blocking external-cache rule. No user/production database was touched. HEAD/live GitHub main and the peeled Day 2 tag target are unchanged; the index is empty and only the eight intended Prompt 4 files are modified/new. P0 = 0; P1 = 0.

No generic plugins, new executable tool, maintenance execution, production identity/RBAC, distinct authenticated consent, cloud/MCP, workers/HA, automatic retry/reconciliation, Unknown resolver or distributed exactly-once claim. No Prompt 5/6 or Day 4 work. External benign pytest cache retention is non-blocking only under the existing frozen rule: outside repository, no active owner/listener/credential or user/production data. No broad cleanup, user file deletion or cache-deletion claim is made.
