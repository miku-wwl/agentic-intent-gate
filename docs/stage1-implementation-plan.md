# AgenticIntentGate — Stage 2 implementation plan

**PLAN ONLY — no files/components below exist yet.** This is the deferred reliability/cloud roadmap. [ADR-011](adr/ADR-011-day1-planning-preview.md) supersedes the former blanket 2027 deferral only for the earlier local planning sprint; use the [Day 1 implementation plan](day1-implementation-plan.md) for the next prompt. This document's 2027 entry review and explicit authorization remain required before its Phase 1; the preview does not claim to complete any phase below. This document does not authorize implementation, dependency installation, cloud access, commit, or push. Expected paths are prospective, not created directories.

## Stage 2 Entry Review — 2027

Before implementation begins, perform a short ecosystem revalidation against current primary documentation, SDK/package evidence, and the accepted frozen contracts:

1. MiniStack Bedrock API support and fidelity for the boundaries this project uses.
2. MiniStack Bedrock AgentCore Runtime support and fidelity for invocation, sessions, and error behavior.
3. Strands Agent API compatibility and a maintainable supported version.
4. Current AWS Bedrock API changes relevant to the planned Model and tool-use adapter.
5. Current Bedrock AgentCore API/runtime protocol, Python version, packaging, and lifecycle changes.
6. boto3, botocore, and Python version/platform compatibility.
7. Whether the existing ADR decisions still hold against those changes.
8. Whether any ADR is materially stale and needs a new or superseding ADR before implementation.

This is a bounded entry gate, not an architecture redesign. Preserve existing ADRs and the frozen contracts unless current evidence makes a decision materially stale; do not silently rewrite historical ADRs. MiniStack is not required by the frozen architecture. If its Bedrock and AgentCore fidelity has materially improved and provides useful local validation, record its optional adoption and affected test boundary in a new or superseding ADR before Phase 1. If it adds insufficient value, continue with the frozen architecture without MiniStack. No Phase 1 work begins until this review is recorded and Stage 2 is separately authorized.

Use one future `src/agentic_intent_gate/` package with domain, application and adapter modules. Keep stages small and runnable from deterministic fixtures. A phase PASS requires its acceptance evidence; unresolved safety failures cannot be deferred by renaming them local PASS.

## Phase 1 — durable core and storage

- Objective: implement contracts, canonicalization, state transitions and SQLite atomic StateStore operations.
- Expected later: `domain/{contracts,identity,state,failures}.py`, `ports/state_store.py`, `adapters/sqlite_store.py`, minimal package/lock, contract fixtures/tests.
- Tests: canonical byte/hash vectors including duplicate JSON keys and defaults; every allowed/forbidden transition; transaction rollback, unique event/Action constraints, version conflict; fresh reopen sees commits; WAL/FULL settings inspected.
- Acceptance: named operation semantics and immutable bindings pass with real file-backed storage; no network or application cloud credentials.
- Non-goals: Agent, external tools, HTTP, approval UI, cloud deployment.

## Phase 2 — event acceptance and Turn identity

- Objective: accept one event atomically, reserve Conversation slot, map exact duplicates.
- Expected later: `application/accept_inbound.py`, Conversation registry/acceptance use cases, CLI fixtures.
- Tests: duplicate same key/hash, changed actor/content/destination conflict, missing key428, lost commit response, two same-conversation acceptance contenders, cross-conversation acceptance, crash A and reopen.
- Acceptance: one event creates exactly one Turn and audit/slot; losing new event creates nothing; duplicate produces zero new model/write/send operations.
- Non-goals: executing tools or background worker.

## Phase 3 — Strands planning and read tool

- Objective: retain real Agent/tool calling with deterministic Model, authorized reads, proposal-only mutations.
- Expected later: `adapters/strands_planner.py`, `adapters/models/contract_model.py`, read gateway, tool registry and read fixture.
- Tests: real Strands tool-use/result events; per-Turn reconstructed committed history; malformed calls/unregistered tools denied; read budgets; proposal wrappers never access mutation adapter; all model request caps, provider hidden retry configuration, crash B/C.
- Acceptance: read-only Turn finalizes from valid read result; a complete bounded planning snapshot can commit once; fresh planning attempts cannot cause writes; no native reference compatibility alias/code copied.
- Non-goals: real LLM, FileSessionManager as authoritative state, result-dependent mutation loop.

## Phase 4 — controlled Action execution

- Objective: commit immutable multi-Action plan and execute authorized non-approval mutations through the gateway.
- Expected later: `application/action_gateway.py`, adapter capability/result contracts, independently persistent service fixture, append-note adapter.
- Tests: claim transaction before I/O; same-key replay receipt; argument/version/hash mismatch; provider effect+receipt atomicity; two plans conflicting at commit; permanent external failure; external revision failure contract; known no-effect retries/budgets.
- Acceptance: observable external note effect/receipt and local Action result; committed plan never replans; claim loser never writes. APPROVAL_REQUIRED tools are denied until Phase 5 is accepted.
- Non-goals: production downstream APIs, compensation, exactly-once claim.

## Phase 5 — authorization and approval

- Objective: implement durable proposal-bound human decision and controlled resume.
- Expected later: `application/approval.py`, policy registry, signed assertion verification adapter, CLI decision/query commands.
- Tests: unauthorized actor/approver, requester approving itself, wrong hash, changed arguments, expiry boundary, duplicate/contradictory decisions, cancellation, revocation race guarded at claim, pending plan prevents all mutation, restart D/E/F.
- Acceptance: exact authorized consent survives reopen; approval/Action/receipt/audit update atomically; all required approvals before first write; fresh authorization enforced at claim.
- Non-goals: frontend, production IdP/SSO, consent renewal/editable Actions.

## Phase 6 — ownership and conversation races

- Objective: broaden concurrent execution and stale-worker evidence beyond basic claim checks.
- Expected later: concurrency fixtures/barriers, multi-process race tests, recover/claim commands.
- Tests: two independent processes claim same Action; paused old executor; fenced plan commit/result; same conversation waiting approval; sequential Actions; distinct conversations; cross-conversation external revision conflict.
- Acceptance: one eligible claim per attempt, zero I/O by loser, no deadline-driven takeover, no global provider chronology claim.
- Non-goals: queues/FIFO/global scheduler or cross-region locking.

## Phase 7 — crash recovery and UNKNOWN

- Objective: recover durable state and capability-specific uncertainty without blind retry.
- Expected later: `application/recovery.py`, reconciliation evidence/quiescence contracts, crash barriers, adverse tool fixtures.
- Tests: real process termination at A–H and J; external effect survives gate death; LOOKUP_AND_KEYED/LOOKUP_ONLY/KEYED_ONLY/NONE; delayed/inconclusive lookup; quiescence required; expired key guarantee/approval; lost result persistence; current permission denial.
- Acceptance: receipt proves H success without new effect; G conservatively becomes UNKNOWN; non-reconcilable uncertainty stays blocked; no stale result overwrite or reopening terminal Action.
- Non-goals: automatic rescheduler, arbitrary workflow replay, evidence-free manual override.

## Phase 8 — independent delivery

- Objective: atomically finalize Turn/Delivery/slot and send immutable summary.
- Expected later: `application/delivery.py`, independent durable mailbox fixture, delivery attempts/reconciliation.
- Tests: crash before/after finalization; crash I and lost sender; known no-acceptance versus UNKNOWN; keyed receipt/retry; payload mismatch; Action not rerun; out-of-order summary visibility includes Turn sequence.
- Acceptance: terminal Turn always has one final Delivery; SENT means receiving-provider receipt; send failure does not undo confirmed Action.
- Non-goals: real SMS/email, read receipts, streaming response push.

## Phase 9 — local HTTP and complete evidence

- Objective: expose use cases through loopback HTTP/CLI with security, audit, structured logs and status semantics.
- Expected later: `adapters/local_http.py`, CLI commands, composition/configuration, local run guide and end-to-end tests.
- Tests: authentication negative cases/algorithm/key/audience/body binding; 200 terminal failed snapshots, 202 waits, 409 slot/hash races, 428 missing key; client timeout and duplicate lookup; authenticated resume/recover/approval; all crash A–J; audit/log correlation chain and network guard.
- Acceptance: one documented local run proves every charter MVP requirement using real Agent, real gate storage, independent persistent tools/mailbox, effect counts and process restarts. Archive run ID, versions, operation traces and cleanup scope. Mark PASS_LOCAL only.
- Non-goals: AWS calls, externally exposed server, frontend.

## Phase 10 — optional exploratory local model

- Objective: if explicitly wanted, adapt Foundry Local or another local provider while retaining the contracts.
- Expected later: one Model adapter and exploratory script/configuration.
- Tests: provider capability mapping, tool-use event parsing, no direct mutation bypass; deterministic suite remains default and independent.
- Acceptance: exploratory read/proposal case works with documented limitations, or mark this optional phase SKIPPED; local stochastic output is not correctness evidence.
- Non-goals: making correctness depend on local model availability or adopting MiniStack without the 2027 entry review and an approved ADR.

## Phase 11 — AWS adapters without resources

- Objective: implement DynamoDB StateStore, controlled demo-table tool/mailbox adapters and Bedrock request mapping.
- Expected later: `adapters/dynamodb_store.py`, `adapters/tools/dynamodb_demo.py`, `adapters/dynamodb_mailbox.py`, `adapters/models/bedrock.py`, SDK request fixtures.
- Tests: same named-operation behavioral suite; exact transaction conditions and no two operations against same item; lost response/receipt reload; strong read flags; declaration/hash/key/permission boundaries; optional fresh LocalStack API parity lane; Bedrock retry/time/token/request mapping with network blocked.
- Acceptance: deterministic adapter/request suite passes; emulator result labeled separately if run. Real AWS capability remains NOT VERIFIED. If emulator unavailable, unresolved service parity is an explicit cloud gate requiring pre-action service checks.
- Non-goals: resource provisioning, loading real credentials, live Bedrock call, another project's LocalStack state.

## Phase 12 — AgentCore and deployment package

- Objective: add the managed runtime entrypoint and reproducible Python 3.13/Linux ARM64 CodeZip/package plan.
- Expected later: `adapters/agentcore_entrypoint.py`, runtime composition, scoped packaging/deployment/teardown plan and prospective IAM policy files.
- Tests: local SDK `/invocations`/`/ping`, operation envelope/domain errors, actor assertion/session membership, independent runtime reconstruction from store port, pinned dependency/build metadata, architecture/platform checks, IAM policy review/negative request fixtures.
- Acceptance: reviewable package/digest/config/IAM/budget/cleanup plan prepared without deploying. Fresh process reconstructs correctly; no app state in managed sessionStorage. Exact role/service compatibility still requires real AWS validation.
- Non-goals: deployment before authorization, Terraform, generated reference infra, public webhook/voice.

## Phase 13 — narrow authorized real AWS validation

- Objective: validate the selected service boundaries within an explicitly accepted account/Region/resource/model/spend scope.
- Expected later: evidence/runbook appendix with named resources, package digest, exact IAM/credential scope and teardown results.
- Tests: first validate real DynamoDB atomic conditional acceptance/claim/decision/receipt parity and negative IAM permissions; then one happy path, one approval path, one duplicate/idempotency case, one controlled restart/reconstruction case, one controlled failure/UNKNOWN receipt recovery, and one joined CloudWatch trace/log query. Use at most 20 total model requests under token/time/spend caps.
- Acceptance: archive actual external demo effect/receipt, gate states/audit, fresh runtime reconstruction evidence, and cleanup query results. Report verified cases individually; a failed cloud gate cannot be relabeled by local tests. Actual carrier/Twilio/production semantics remain outside scope.
- Non-goals: broad soak/load test, always-on environment, production launch, voice, third-party APIs.

## Phase boundaries and review gates

Do not skip atomic acceptance/claim/approval foundations to produce a superficial happy path. Adding paths/tool adapters does not weaken the denial of unsupported risk classes. Tests should attack contracts and actual side effects rather than mirror implementation methods. Re-run broader tests only when changes or new failure evidence justify it.

Before real AWS: mandatory local MVP evidence is accepted; cloud adapters/package/IAM are reviewed; a current budget and credentials/resource scope is explicitly authorized; pending UNKNOWN records are isolated from teardown; deployment/cleanup ownership is clear. Architecture acceptance alone does not grant cloud or Git delivery authorization.
