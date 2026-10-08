# Day 3 Prompt 3 — verified local authorization and controlled execution

Final gate: `DAY3_PROMPT3_AUTHORIZATION_EXECUTION=PASS`. The final complete run passed 1002 unique deterministic cases, with zero failures/errors/skips and five live deselected, including all 77 existing browser cases. [Evidence](evidence/day3-prompt3-authorization-execution.json) retains the measured development and verification chronology. No Git delivery was performed.

Review base: `4dd8bafdf7992bafea29e796e220c5cbc4b95609`, `main`, initially clean and synchronized. Prompt 2's committed gate is PASS with 850 deterministic cases. The immutable `day2-final-freeze` tag remains `c7d3e1ee051af40c69ec33d888ecf16475a2d789`. Authority: [Day 3 freeze](day3-action-execution-architecture-freeze.md), [ADR-013](adr/ADR-013-action-ledger-execution-boundary.md), [ADR-014](adr/ADR-014-approval-plan-binding.md), [implementation plan](day3-implementation-plan.md). These documents, the nine-table SQLite v2 physical schema/migration and the four JSON schemas are unchanged. No commit, push, tag or release.

## Distinct commands and truth

The supported path is exactly one entire, newly exact-bound Approved/v5 single-step `append_service_note/1`, SIDE_EFFECT, `service-demo` proposal. Ready/read, maintenance/APPROVAL_REQUIRED, multi-step and legacy-unbound proposals remain denied. No model is called by Action operations. Approved is not Authorized or Executed.

| Explicit boundary | Durable facts | Calls/effects |
| --- | --- | --- |
| Admission | Pending/v1 Action + creation event + scoped receipt | None |
| Authorize | Exact-spec grant + Authorized/v2 + event + receipt | None |
| Execute claim | Executing/v3 + one immutable attempt + claim event + receipt | None before confirmed commit |
| Gateway dispatch | One reviewed adapter invocation outside gate transaction | Separate demo note/receipt transaction |
| Completion | Succeeded, Failed or Unknown/v4 + bounded result + matching-attempt event | No retry |
| Startup recovery | Abandoned Executing -> Unknown/v4 once | No model, tool or receipt lookup |

Run remains Approved/v5. Receipts, grants and attempts cannot bypass CAS or immutable approval/spec proof. There is no generic lifecycle setter or terminal resurrection. In accordance with the frozen architecture, attempts are immutable claim evidence, **not** a second mutable result state machine. Action.result and its matching-attempt event are authoritative; no new SQL fields/v3 migration were introduced to duplicate an “attempt result.”

## Capability, principal and current policy

`local_capability.py` provides an explicit operator-only provisioning function using `secrets.token_hex(32)`: 256 unpredictable bits encoded as 64 lowercase hex characters, stored only in a new private file. It uses the existing exclusive-create/privacy utility (POSIX 0600; Windows inherited ACL removed/current SID only). Provisioning never prints or returns the credential and refuses an existing file. Protect its parent directory as well. This is a private local operator channel, not user registration, person identity, distinct-approver consent or RBAC.

The verifier has no default token or permissive principal. Missing/unconfigured/invalid credentials return AUTHENTICATION_REQUIRED. Only one exact `Authorization: Bearer ...` header is accepted; verification compares SHA-256 credential fingerprints with `hmac.compare_digest`. The verifier retains the fingerprint, not raw bearer material. Server configuration supplies the stable subject, execution_scope and fixed operation allowlist (`read`, `admit`, `authorize`, `execute`). Immutable PrincipalContext contains no bearer credential; the in-process issuer identity prevents a body/dictionary/new context or altered scope from masquerading as a verified context.

The application and repository independently require operation permission before receipts or mutations. LocalDemoAuthorization checks the complete exact supported tool/risk/target/binding/scope and returns a configured policy revision. Default AuthorizationPort denies. An authorization row binds server UUID, Action, verified subject, spec_digest, policy revision and strict timestamp. Execute freshly verifies permission and requires the same grant subject/spec/policy. Changed policy, retired binding or another subject/scope cannot dispatch. No hot configuration, grant renewal or credential rotation while a call is in flight is supported: stop/drain the predecessor first.

Credentials are not accepted from query strings, cookies, Plan, arguments, body actor/role or LOCAL_USER. Managed credentials do not enter model context, static HTML or response/result/events; production request access logging is disabled. A failed test's diagnostic fixture repr exposed an ephemeral random test token, not a permanent/operator credential. The fixture now has a credential-omitting repr, and four occurrences in the retained failed JUnit report were redacted without altering its failure count. Final candidate/package scans and private-file teardown checks passed. User note prose is data, not automatically secret-filtered or encrypted. Host administration and wholesale evidence rewriting remain trusted-host limitations.

## Explicit local preparation and startup

The unchanged `aig-plans`/`main.py` is still strict v1 and has no Action routes. `python -m agentic_intent_gate.execution_main --predecessor-stopped` is the separately selected v2 demo entrypoint. No dependency or console-script change is needed.

Before selecting it, stop **all** previous app/helper owners. An existing v1 DB requires the already documented explicit offline migration and private v1 backup; startup never migrates or resets it. Select a fresh v2 path or valid existing v2. Never use production/user data for the demo.

| Required server configuration | Meaning |
| --- | --- |
| Existing LOCAL_LLM_BASE_URL / LOCAL_LLM_MODEL | Unchanged loopback planning configuration; startup/recovery makes no inference |
| AIG_DB_PATH | Explicit local gate database, no v2 default path |
| AIG_DEMO_DB_PATH | Separately configured isolated demo file, distinct from gate (including resolved links/hardlinks) |
| AIG_EXECUTION_SCOPE | Stable canonical resource UUIDv4, provisioned once and retained in downstream metadata |
| AIG_CAPABILITY_FILE | Operator-provisioned private 64-byte credential file, not a URL or repository asset |
| AIG_SUBJECT_ID | Stable server-owned bounded local-demo subject |
| AIG_POLICY_REVISION | Stable bounded server-owned policy revision |

Use `provision_capability(path)` and `provision_demo_store(path, execution_scope)` in a private local operator session, with new files in a trusted private directory. The provisioner only creates the chosen isolated local persistence resources. This task did not provision any permanent operator credential or start a user-facing execution server. Tests use random transient credentials and external temporary paths. On Windows the private file is created with the restricted ACL; operators supplying an independently created file are responsible for equivalent private ACL/parent protection. POSIX composition also rejects group/other permissions. Symlink credential paths, missing/malformed/private configuration and absent quiescence attestation are rejected safely without echoing paths or secrets.

The execution entrypoint holds an OS nonblocking file lock for its entire serving lifetime, preventing another cooperating execution owner from starting. It also requires the explicit predecessor/helper-stopped attestation; a DB lock or request timeout is not proof of helper quiescence. The reviewed default adapter creates no thread, subprocess or detached worker. Direct library/test composition must hold the same ownership contract; the flag is not an automatic universal process detector. Validate complete v2 data, recover Runs and Actions, then serve on the unchanged loopback host. Access logging is disabled so URLs/headers are not diagnostics.

Do not recreate a missing/replaced store under the old UUID or delete its receipt history. Store identity and receipt continuity are a provisioning/retention contract, not an identity a new empty file inherits. A changed configured scope/binding blocks claim; a changed actual store identity is detected by the adapter before any effect. A privileged host that rewrites the entire resource and all evidence is outside this local guarantee.

## Ledger, idempotency and historical binding compatibility

Each named write uses a short BEGIN IMMEDIATE, exact v2 schema/history validation, guarded state/version CAS, paired event and accepted command receipt. Authorization/claim failure rolls back every linked fact. A claim result carrying an attempt ID is returned **after** confirmed COMMIT; even an injected exception after an actual commit produces no dispatch permit. Attempt IDs are server UUIDv4, unique per Action; one attempt is the entire MVP budget.

Result persistence reloads intact approval/spec/grant/attempt evidence and requires Executing/v3 plus the exact attempt ID. Update and terminal event commit together. A late/wrong completion or recovered Unknown cannot overwrite state or append another event. Rollback leaves Executing; an uncertain commit may already contain the terminal result. Return a safe database error and lookup handle, never invent Failed or send again.

Scoped receipts are `(subject_id, operation, resource_id, Idempotency-Key)` with SHA-256 canonical exact-body fingerprint including expected version. Verified current permission precedes receipt lookup. Accepted exact replay precedes CAS and returns the current snapshot without another grant, attempt, event or call. Changed body conflicts; another key cannot bypass version/state/attempt uniqueness. Different Runs are distinct intentional effects, not semantic Intent deduplication.

The committed Prompt 2 bug was independently reproduced from `git show HEAD`: binding A admission followed by configured B made historical repository initialization fail DATABASE_UNAVAILABLE. Historical reconstruction now validates retained versioned binding metadata, full Plan/approval proof, canonical spec digest, immutable effect key, grant/attempt/event/receipt linkage independently of the **current** registry. It does not call the new-spec compile entry point, repair/rewrite an old spec or fabricate a callable. A->B and empty-registry reads/reopen/list preserve every row. Current admission, authorization and claim still strictly require the configured binding/policy; retirement never grants execution. Corrupted retained fields remain denied; unsupported historical compiler contracts fail closed.

## One reviewed executable adapter and truthful outcomes

Metadata remains non-callable. ExecutableRegistry has only the reviewed DemoNoteExecutor binding (`demo-note-adapter`, `demo-binding-v1`) for append_service_note/1 and rejects arbitrary callable/plugin routing. Compiler metadata must match that registry in gateway composition; the default registry denies. No URL, shell, filesystem destination or dynamic import comes from client/model/note arguments.

ToolExecutor receives immutable narrow ExecutionSpec, stable server effect_key and AttemptContext (persisted attempt ID, bounded deadline, cancellation event). It receives no raw Run/HTTP/model output, principal credential, gate connection or generic environment. Arguments preserve exact note text, service_id, trusted target and existing 1024-character/4 KiB limits. Gateway waits at most five seconds for adapter work inside a ten-second HTTP command budget; short local persistence is separate. Disk/OS faults have no hard timing guarantee, and cancellation is not a universal external-effect stop guarantee. No detached or retried call follows a response.

The demo opens its existing separately owned file with mode=rw, verifies schema/resource identity and receipt/note linkage, and atomically inserts a note plus unique effect-key/payload-digest/UUID receipt. Parameterized SQL treats hostile strings as data. Same key/same exact payload returns the retained receipt without append; another payload is rejected. There is no ATTACH, gate connection reuse, cross-store atomicity or general exactly-once claim.

| Evidence after dispatch | Gate result |
| --- | --- |
| Durable keyed receipt matching scope/target/exact payload and opaque UUID | Succeeded / EXECUTION_SUCCEEDED |
| Reviewed authoritative input/identity/payload rejection before an intended effect | Failed / EXECUTION_CONFIRMED_FAILED |
| Timeout, cancellation, generic exception/SQLite fault, malformed evidence or unconfirmed downstream commit | Unknown / EXECUTION_OUTCOME_UNKNOWN |

Result messages are fixed frozen strings; only success has a canonical opaque receipt reference. No adapter message/body/SQL/path is copied. A downstream commit-response-loss fixture proves one real note/receipt with gate Unknown, not false Failed/Succeeded. Gateway CAS plus a retained provider key supports one local dispatch opportunity and at most one demo note per retained key; it does not promise exactly-once arbitrary external effects or eventual delivery.

## Guarded HTTP and recovery

The opt-in composition exposes only the five frozen APIs: POST/GET `/api/runs/{run_id}/actions`, GET `/api/actions/{action_id}`, POST `/api/actions/{action_id}/authorize`, POST `/api/actions/{action_id}/execute`. All require verified capability/operation permission; reads also require scope visibility. An enabled gateway without a verifier returns 401, never an administrator fallback. Existing Run/approval/preview APIs and HTML are unchanged.

Mutation JSON has only expected_run_version or expected_action_version. Strict UTF-8, 16 KiB body cap, no duplicate/unknown fields or numeric coercion, canonical UUIDv4 paths and Idempotency-Key apply. Nonlocal or other-origin browser requests are denied where Origin is present; no credentialed CORS/cookie authority. GET causes no compilation of a new spec, authorization, recovery, receipt, event or effect. Frozen safe error envelopes/statuses are preserved. Successful execution outcome snapshots use 200 even for Failed/Unknown; exact accepted replay still Executing uses 202, not another dispatch.

Before serving, recovery validates the complete ledger and, only under stopped ownership, commits Executing->Unknown once. Pending, Authorized, Succeeded, Failed and Unknown are unchanged. Repeated recovery converges. It makes no model/tool/downstream status lookup and does not authorize, reset, replay, retry or reconcile. Wrong/late attempt completions remain fenced.

Focused deterministic A-E windows cover committed claim/no call; entered dispatch/interruption; durable success before local result; confirmed no-effect before result; and local result commit/response loss. Recovery is reopened file-backed and checked for three converged passes with unchanged downstream facts. These are controlled boundary/failure fixtures, **not** a new killed-process/power-loss execution matrix. The existing real Day 2 killed-process regression is preserved; exhaustive execution process/crash hardening belongs to Prompt 5.

## Verification and remaining scope

Foundation gates passed before later unsafe work: historical binding/admission 100; capability 10; authorization 17; adapter 11; gateway 21; HTTP 35; recovery 12. The expanded final focused regression passed 314 unique tests (164 existing Prompt 2 plus 150 Prompt 3). Do not add overlapping phase counts. First gateway collection had 21 fixture-registration errors, fixed before its PASS. Expanded corruption tests then had six safe-error-category failures (307 passed); damaged retained tool fields are now classified as storage corruption and the final focused run passed. Lint/format corrections are recorded as development corrections, not final gate failures.

The initial complete run passed 1000 cases. Final review then added two Action-store schema-fault message cases; both initially failed because the HTTP boundary used the Run-store safe message. The Action-only normalization was corrected and all 59 HTTP cases passed. The next full run had 1001 passed and one concurrency-fixture failure: an independent 10 ms write-lock probe collided with another legitimate claim transaction before the real adapter call. The gateway truthfully stored Unknown with one attempt and zero downstream notes. A test-only bounded barrier now waits for all 20 competing claim transactions to close before that independent transaction-boundary probe. Product CAS, deadlines, dispatch and no-retry behavior were not relaxed. The 114-case execution/HTTP/authorization subset passed; both race cases also passed in three consecutive stability runs. The ephemeral diagnostic token exposure and redaction are recorded above.

The final complete run is `.venv/Scripts/python.exe -m pytest -q --tb=short --junitxml=.runtime/day3-prompt3/final-stable-tests.xml --basetemp=D:/Ollama/Temp/aig-day3-prompt3-final-stable-tests`: 1002 unique deterministic cases, zero failures/errors/skips, five live deselected, JUnit elapsed 516.500 seconds. New modules: authorization 21, binding compatibility 2, capability 10, execution 34, HTTP 59, recovery 12, adapter 14: 152 new cases. The existing 850-case baseline is preserved, including 77 browser cases and the real Day 2 restart lane. No new live Ollama/cloud call was made. Overlapping subsets and repeated runs are not added to the unique count.

Offline wheel/sdist, a new installed environment at `D:/Ollama/Temp/aig-day3-prompt3-package-verify/venv`, and the installed-wheel probe passed. The probe verified 45 runtime and four schema resources against source bytes, existing planning/migration/bound approval/admission, protected authorize/execute, exactly one actual downstream note/receipt and replay/reopen with no second call. Its four provider calls are controlled, not live; one adapter call is scoped to that positive fixture. Artifact archives contain no DB/WAL/SHM/bytecode/runtime/venv/private credential or traversal member. The final documentation/evidence snapshot is included by a final offline archive refresh; no product-runtime change or repeated full suite is needed for that documentation-only refresh.

The final browser evidence is `.runtime/browser-evidence/20261008T040809531927Z/results.json`: pytest exit code zero, 77 passed, Edge 154.0.4258.53 headless, four closed browser/driver records and 77 closed HTTP servers. Live checks found zero listening recorded ports or task-owned test processes. Repository runtime DB/WAL/SHM count and remaining private test credential count are zero. Two early random fixture credentials were explicitly removed after verifying owner-only protected ACLs (one allow entry, zero inherited entries); subsequent credentials are removed by fixture teardown. No user credential or production/user DB was touched.

At the final cleanup audit, 3107 synthetic database/WAL/SHM files remained across 25 task-owned external pytest/package roots (including corruption/failure fixtures), plus the isolated package environment. They are recoverably retained, **not claimed deleted**, under the frozen external-cache non-blocking rule: outside repository/package, no credential, active owner/listener or user/production data. Build/results remain ignored in `.runtime/day3-prompt3`. No global or unrelated cleanup is required.

No Turn/new Run states, unreviewed schema migration, production identities/RBAC/distinct consent, APPROVAL_REQUIRED execution, cloud/MCP, generic plugin, worker/HA, automatic retry/reconciliation, Action UI, Prompt 5 exhaustive execution crash matrix or Git delivery. The existing Action and approval-bound architecture is extended only by the requested local controlled execution slice. Production security/durability, malicious-adapter sandboxing, host-level tamper resistance and universal physical cancellation are not claimed.
