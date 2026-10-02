# Day 1 — repository audit

Audit date: 2026-10-03, Pacific/Auckland. This is current repository evidence, distinct from archived reference reports and current host observations.

## Baseline and coverage

- Root: `D:\workshop\oct\agentic-intent-gate`; branch `main`; HEAD `4df3bbe287e5e3b4a8527dee3de82451c629f70b`; initially clean.
- Origin: `https://github.com/miku-wwl/agentic-intent-gate.git`. No fetch, commit, push, or remote-state claim is part of this audit.
- All 30 tracked files were inspected: README, handoff, eleven Stage 1 documents, ten ADRs, five Stage 0 reports, and two Stage 0.5 reports. The non-Git recursive inventory also contained only these files. No applicable AGENTS.md was found in the workspace or its ancestor directories.
- There is no backend/frontend source, test directory, script, CI configuration, dependency manifest/lock, migration, environment example, executable schema, or Foundry/phi-4-mini implementation at baseline. Text searches covered model configuration, schemas, TODO/TBD markers, fake transports, and phase/schedule contradictions. No product TODO list exists; outstanding work is in the roadmap and evidence gates.

## Classification

`IMPLEMENTED` requires repository artifacts for the capability. `PARTIAL` requires some product implementation. `DOC-ONLY` is an explicit design without implementation. `MISSING` means no product artifact. `STALE` means a historical claim or instruction does not describe the current sprint/checkout. A host capability is not a repository implementation.

| Area | Baseline classification | Evidence / practical meaning |
| --- | --- | --- |
| Backend / API | MISSING | No Python source, entrypoint, route, or server configuration; `POST /api/plans` does not exist |
| Frontend / UI | MISSING | No HTML/JS/TS, package.json, assets, or browser application |
| ModelProvider | DOC-ONLY | Stage 1 architecture and ADR-009 specify a Strands Model boundary, not an adapter or application protocol |
| Repository local LLM integration | MISSING | Foundry appears only as optional later work; no endpoint/model configuration or phi adapter |
| Deterministic validation | DOC-ONLY | Stage 1 strict tool schemas, registry risk, canonical arguments, caps, and fail-closed rules are prose; no executable validator/schema at baseline |
| Product test coverage | MISSING | No test files/runner; coverage is unmeasured, not a passing or numeric coverage result |
| Browser E2E | MISSING | No frontend, browser test, or browser execution evidence |
| Persistence | DOC-ONLY | ADR-002 and named StateStore transaction groups select SQLite/DynamoDB; neither adapter nor database exists |
| Approval / execution / recovery / delivery | DOC-ONLY | Detailed contracts/state machines and ADR-003–007; no gateway, decision endpoint, claims, fixtures, or effects |
| Current project mocks | MISSING | No scripted Model or fake tool/provider implemented in this repository |
| Source study / architecture documentation | IMPLEMENTED (documents only) | Reports and ten ADRs exist; this does not imply implemented product behavior |
| Reproduction harness / archived test claims | STALE as current-run evidence | Handoff names reproduction source, evidence, lockfile, MANIFEST.sha256 and REVIEW_README absent from this checkout |
| Schedule / scope wording | STALE for current sprint | 2027 blanket deferral, no UI, and optional-local-model sequencing are scoped by ADR-011 |

No product capability qualifies as PARTIAL: there is no product code to classify that way. No dead executable code was found. Missing archived files are documented rather than reconstructed or imported from another project.

## Reference reports and mocked boundaries

The seven `research/` reports remain historical evidence. Stage 0 inspected pinned Twilio source and dependencies without running product tests. Stage 0.5 reports eight local reproduction tests using real reference Lambda/TAC/Strands/SDK boundaries, a scripted Model, fake AgentCore transport, fake Twilio HTTP, and local file history. Its native imports failed 1/8 until an explicit export alias; its safe Python subset skipped five tests with unmocked network paths. These reports do not establish current product tests, real model inference, live cloud delivery, or durable workflow recovery. Their harness and raw results are not available here to rerun.

`STAGE1_HANDOFF.md` says the full package includes source/tests/lockfile/evidence and references a manifest and REVIEW_README. README and Stage 1 review already qualify those as absent archived references. Preserve the supplied handoff and research unchanged. The Stage 1 review's HEAD `546b35c...`, dirty worktree, and document counts describe its historical run; they are not today's baseline.

## Existing frozen architecture

Keep Python and the service operations notebook vocabulary. Later execution uses a real Strands proposal planner, complete immutable plan commit, trusted registry risk/permissions, one ActionExecutionGateway, distinct exact-proposal approval, stable identities/idempotency keys, conditional ownership, explicit UNKNOWN, and separate delivery truth. Local SQLite and later DynamoDB remain selected for that execution system. No universal exactly-once guarantee or automatic deadline-driven takeover is claimed.

Day 1 defines only a planning preview before those durable records. [ADR-011](adr/ADR-011-day1-planning-preview.md) records the scoped schedule/provider/UI changes openly. Five transient preview states do not replace the durable Turn/Action/Approval/Delivery machines.

## Host local model verification

Read-only inspection used installed CLI help, `foundry --version`, `foundry server status --output json`, `foundry model list --loaded --search phi-4-mini --output json`, the cached-model equivalent, and `GET /v1/models`. CLI 0.10.3 uses `server`, not the older `service` command; the initial `service --help` attempt returned an unknown-command error, then inspection used the actual help output. No service was started/restarted, model loaded/downloaded, or configuration changed.

Observed service: ready, PID 2260, base URL `http://127.0.0.1:39839`. CLI listed loaded/cached alias `phi-4-mini`, catalog ID `Phi-4-mini-instruct-generic-gpu:5`, tool-calling flag true. The actual `/v1/models` wire ID is `Phi-4-mini-instruct-generic-gpu` (without `:5`). These are audit-time host observations; resolve them again before implementation and do not hardcode the port/catalog ID as a wire contract.

One independent direct `POST /v1/chat/completions` probe used that wire ID, temperature 0, max_tokens 512, streaming false, and a read-record preview prompt. HTTP completion returned `finish_reason=stop` in 6.968 seconds. The JSON used the correct tool/arguments but emitted `risk_class="Information disclosure risk"`, outside the required enum. The frozen schema rejects it. Thus local inference works for this request, while valid structured-plan generation and repository integration remain NOT VERIFIED. See [probe evidence](evidence/day1-local-model-probe.json).

Microsoft's [Foundry Local REST reference](https://learn.microsoft.com/en-us/azure/foundry-local/reference/reference-rest) documents the chat-completions path and model discovery. The inspected page does not establish enforced `json_schema` support for this installed model/service. Do not infer constrained decoding from compatibility or a tool-calling catalog flag. Day 1 uses a schema-bearing text prompt plus independent validation; no `response_format` capability is required.

## Day 1 additions and verification boundary

The [freeze](day1-architecture-freeze.md), [next plan](day1-implementation-plan.md), ADR-011, two JSON schemas, and probe record make the next task reviewable. There is still no application source or server. Schema checks validate the contract and reject the captured invalid model result; they are not backend, live-provider integration, browser E2E, durability, or cloud tests. Research and handoff are compared with baseline Git blobs, and HEAD/index must remain unchanged.

Final [quality-check evidence](evidence/day1-quality-check.json): two schema definitions valid; 41/41 schema conformance cases matched expectations; captured live output rejected; seven cross-field/policy examples confirmed to require the future semantic validator; 72 local Markdown links checked; balanced fences and whitespace checks passed. All seven research reports and the handoff retain their exact start-of-task SHA-256 bytes, and the ten original ADRs are unchanged. Five historical Stage 0 workspace files already use CRLF versus Git's normalized LF; raw preservation was verified against the start-of-task hashes. No product/runtime suite exists to run, and no browser E2E or cloud behavior is verified.
