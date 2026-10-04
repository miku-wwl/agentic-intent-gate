# Day 2 — repository audit before state design

Date: 2026-10-04, Pacific/Auckland. **Day 1 IMPLEMENTED / FROZEN; Day 2 runtime MISSING.** This audit covers architecture/contracts only, under [ADR-012](adr/ADR-012-persistent-runs-local-approval.md).

## Verified entry baseline

- Branch `main`, HEAD `abd695a1085fe52d85ee66cfbaf68717a867eac1`; `git status --short` empty and `git diff --check` passed. The Day 1 final-freeze documentation is already committed in this HEAD; no uncommitted Day 1 files were reset or overwritten.
- The eight checked commits are abd695a, 361b071, fe74fe2, 76a8a70, bc367bf, 4df3bbe, 5723459, 546b35c. The [Day 1 final record](evidence/day1-final-freeze.json) intentionally retains its earlier review HEAD and pre-publication worktree description; it is historical evidence, not a current Git-status claim.
- Python 3.13.9; stdlib SQLite 3.51.0. Existing `uv.lock` check passed (34 packages). No new dependency was installed or lockfile changed.
- Complete entry gate: **191 passed, 4 live tests deselected, 33.44 seconds**. This includes 19 frontend and 6 browser/backend cases using controlled providers, not real phi-4-mini. Ruff check/format passed; 65 Python files already formatted. No live model study or smoke was run for this architecture task.

Read the active README, final-freeze evidence, Day 1 architecture/implementation/run documents and ADR-011; both canonical schemas; domain contracts and strict validator; PlanningService/ModelProvider/provider; trusted catalog; configuration/main/HTTP routes; full static frontend; API/parser/state/provider/browser fixtures and test coverage; packaging and ignore rules. Reviewed ADR-002/004 and the historical execution contracts for precedence. Baseline raw-byte hashes of all 83 tracked files were recorded in ignored review evidence for preservation checks.

## What exists and what does not

| Area | Entry classification | Actual repository evidence / Day 2 gap |
| --- | --- | --- |
| Planning vertical slice | IMPLEMENTED | Real production LocalOpenAIProvider -> strict parser/schema/semantics -> immutable Plan or safe rejection; no tools execute |
| Persistent Run identity | MISSING | No Run class, UUID creation, repository or Run route |
| Run state | PARTIAL | Request-local PlanningState/history exists; restart loses it; no persistent RunState |
| SQLite | DOC-ONLY | ADR-002 selects later SQLite/WAL/FULL; no sqlite3 adapter or product DB exists |
| Schema migration/versioning | MISSING | JSON schemas describe proposals; no database schema/user_version/startup compatibility gate |
| Approval policy | PARTIAL | Trusted risk calculation exists. No requires_approval or local decision routing; Stage 1 approval is only documentation |
| Approval API | MISSING | create_app exposes only GET / and POST /api/plans |
| Audit history | DOC-ONLY | Historical AuditEvent/atomicity design, no stored events or audit repository |
| Restart recovery | DOC-ONLY | Stage 1 durable execution recovery design; no Run startup recovery implementation |
| Optimistic concurrency | DOC-ONLY | Historical CAS/ownership specification, not implemented. Day 1's 20-request isolation test is not a durable version test |
| Stale approval prevention | DOC-ONLY | Deferred proposal/hash/version guards; no local Run version or decision guard |
| Duplicate approval behavior | DOC-ONLY | Earlier Action approval design has command receipts; no implementation. Day 2 explicitly chooses 409 rather than adopting that idempotency subsystem |
| Browser Run detail | MISSING | Existing page displays a transient Plan; no Run navigation, version or approval controls |
| Persistence tests | MISSING | Day 1 package/schema checks do not test committed Run state or SQLite durability |
| Recovery tests | MISSING | No file-backed restart/process-termination Run recovery tests |
| Tool execution/authentication/cloud | DEFERRED | No execution, authenticated actor, RBAC, MCP, cloud adapter or distributed runtime |

## Design decisions needed and frozen

The [state architecture](day2-state-architecture-freeze.md) now fixes server UUIDv4 identity, immutable accepted values, eight compatible state names, all eight allowed edges, four terminal outcomes, conservative write-risk approval, strict decision body, local unauthenticated actor marker, version CAS/conflict precedence, three exact SQLite tables, schema version checks, transaction/audit pairing, interrupted-work rejection and deterministic recovery routing. The two public JSON schemas make shape/policy/state/version constraints checkable without implementing a product service.

The new ADR distinguishes local Run approval from Stage 1's authenticated Action consent and keeps execution guards intact. There is no architecture blocker left open for the proposed Day 2 slice. These documents/schema artifacts are **DOC-ONLY**, not implemented persistence/approval/recovery. Prompt 2 must prove them with new behavioral tests.

## Review risks and honest limits

- CPU-model reliability remains WEAK; persistence records a failure rather than improving stochastic generation or latency.
- No authentication or ownership exists. LOCAL_USER is not an authenticated principal, and local approval grants no tool permission.
- One app process/worker per DB is assumed; startup recovery requires the old process to be stopped. CAS is not a distributed execution claim.
- Run creation is not idempotent; a lost response may leave a Run whose ID the client did not receive. Repeating POST creates another Run and may invoke the model again by that explicit new request.
- Validated proposals use only synthetic service-demo/revision 1; approval does not prove natural-language fidelity, feasibility, live state or a fresh external revision.
- User text/reasons/Plan arguments can be sensitive. No raw diagnostics or provider credentials are stored, but this is not an arbitrary-prose secret scrubber, encrypted vault or administrator-proof audit.
- File-backed persistence, cancellation recording, approval races, commit interruption and actual process-restart recovery are **NOT VERIFIED** until Prompt 2. In-memory DDL/contract checks, if run here, prove only documented syntax/shape/constraints.

## Final architecture-task checks

| Final check | Observed result / scope |
| --- | --- |
| Complete default pytest | PASS: 191 passed, 4 live tests deselected, 32.65 seconds; existing controlled browser cases included |
| Ruff check / format / git diff --check | PASS; index remains empty |
| Public schema definitions / local references | PASS: both new schemas and both unchanged referenced Day 1 schemas, explicit local registry without remote resolution |
| Run contract vectors | PASS: 24 positive / 42 negative; all states, version/risk/approval/Plan combinations, UUID shape and explicit calendar checks |
| Approval contract vectors | PASS: 11 positive / 57 negative; strict decoding, lexical integers, duplicate keys, Unicode/control and field/reason bounds |
| Documented state graph | PASS: 8 allowed / 56 forbidden ordered pairs, four terminal states, no cycles and all paths terminate |
| Prospective DDL | PASS for in-memory syntax, three STRICT tables, version declaration and 29 negative constraint probes only; NOT a product persistence/recovery test |
| Artifact links / whitespace / secret patterns | PASS: 32 local links and all seven scoped artifacts; no high-signal credential pattern matches. This is not a general secret-content guarantee |
| Baseline preservation | PASS: 82 of 83 tracked files byte-identical; only README changed. Product source/tests, Day 1 schemas/evidence, historical ADRs, research/handoff and dependencies/build config preserved |
| Delivery scope | README + six new Day 2 documentation/schema files; HEAD remains abd695a1085fe52d85ee66cfbaf68717a867eac1. DB/WAL/SHM and review helper/evidence paths are ignored |

Ignored review evidence is in `.runtime/day2-prompt1/`: baseline/final test logs, final JUnit XML, baseline hashes and `contract-results.json` with the one-off contract/DDL check. The helper is review-only and adds no product module, repository or persistence test suite.

**DAY2_STATE_ARCHITECTURE_FREEZE=PASS** for the frozen architecture/contracts and preservation gate. Actual file-backed Run persistence, HTTP Run/approval behavior, independent-connection race handling and process-restart recovery remain **NOT IMPLEMENTED / NOT VERIFIED**. No product database, live model call, runtime feature, commit or push was performed.
