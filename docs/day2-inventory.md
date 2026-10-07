# Day 2 Inventory

## 1. Stable Checkpoint

- Branch: `main`; frozen commit: `c7d3e1ee051af40c69ec33d888ecf16475a2d789` (`docs: freeze Day 2 agentic intent gate`).
- Tag: `day2-final-freeze`, annotated at that commit.
- Initial worktree: clean and synchronized with `origin/main` at the inventory start.
- Frozen acceptance: `DAY2_FINAL_FREEZE=PASS`; Prompt 1, 2, 2.5, 3, 4 and 5 are recorded as passed.

Day 2 turns an untrusted Intent into a durable local Run, validates the proposed Plan, recomputes risk from trusted policy, and records a local approval decision for write-risk Plans. **It does not execute tools.** The detailed contract and evidence remain in [the final freeze](day2-final-freeze.md), [architecture freeze](day2-state-architecture-freeze.md), and [final evidence](evidence/day2-final-freeze.json).

## 2. What Day 2 Built

The implemented path is `Intent → Run → one local model planning attempt → strict validation → trusted risk routing → Ready or AwaitingApproval → optional local Approved/Rejected decision`. The existing stateless `/api/plans` preview remains as a compatibility path and has no Run or execution authority.

| Component | Classification | What exists |
| --- | --- | --- |
| `ports/model_provider.py` (`ModelProvider`) | CORE_REUSABLE | Runtime-neutral port; returns untrusted model text, not a trusted Plan. |
| `adapters/models/local_openai.py` (`LocalOpenAIProvider`) | DAY2_SPECIFIC | Local OpenAI-compatible HTTP adapter used with Ollama structured JSON mode; no cloud adapter or SDK. |
| `adapters/configuration.py` | DAY2_SPECIFIC | Server-side loopback model, timeout, and local SQLite path validation. |
| `application/generate_plan.py` (`PlanningService`) | CORE_REUSABLE | One bounded provider call followed by independent parsing and validation; failures are classified safely. |
| `domain/validation.py`, `schemas/day1-plan.schema.json`, `schemas/day1-intent.schema.json` | CORE_REUSABLE | Strict JSON parsing, JSON Schema Draft 2020-12 checks, and semantic/catalog validation. |
| `adapters/tool_catalog.py` and trusted risk policy | DAY2_SPECIFIC | Synthetic `service-demo` / maintenance revision 1 metadata and deterministic risk recomputation; catalog entries are not callable tools. |
| `domain/run.py` (`Run`) | CORE_REUSABLE | Immutable Run snapshot and explicit eight-state transition graph. The state names and transitions themselves are frozen Day 2 contracts. |
| `application/runs.py` (`RunService`) | CORE_REUSABLE | Persists before planning, controls one planning attempt, routes by trusted risk and accepts a versioned local decision. It has no execute method. |
| `ports/run_repository.py` | CORE_REUSABLE | Named persistence operations with version/state guards rather than a generic state setter. |
| `adapters/sqlite_run_repository.py`, `adapters/sqlite_schema.py` | DAY2_SPECIFIC | Local single-node SQLite schema v1, per-call connections, CAS, transaction boundaries, and invariant checks. |
| Approval decision and audit transaction | DAY2_SPECIFIC | A local `LOCAL_USER` marker and one atomic decision/state/version/audit commit; it is not authenticated authorization. |
| `application/recover_runs.py` | CORE_REUSABLE | Deterministic, model-free and tool-free startup recovery through guarded repository operations. Recovery outcomes are frozen to the Day 2 state machine. |
| `adapters/local_http.py` Run routes and `adapters/static/index.html` | DAY2_SPECIFIC | `POST /api/runs`, `GET /api/runs/{run_id}`, and `POST /api/runs/{run_id}/approval`, plus the server-authoritative local Run and Approval console. |
| `POST /api/plans` and its original preview UI | HISTORICAL | Retained Day 1 stateless compatibility behavior; it does not create a durable Run. |
| `tests/`, controlled providers, subprocess fixtures, browser harness, `tests/package_check.py` | TEST_INFRASTRUCTURE | Deterministic service, SQLite, restart, API, browser, and packaging verification; fixtures are not production adapters. |

## 3. Frozen Contracts

- The Run states are `IntentReceived`, `PlanGenerating`, `PlanValidated`, `Ready`, `AwaitingApproval`, `Approved`, `Rejected`, and `PlanRejected`; only the implemented legal transitions are allowed. Terminal states are not resurrected in place.
- Server state, Run ID, version, Plan, and risk are authoritative. Intent and validated Plan are immutable. Every successful state mutation increments version once; GET, rejected CAS, and converged recovery do not.
- SQLite `user_version=1` contains exactly three STRICT tables: Runs, approval decisions, and audit events. Unsupported, partial, corrupt, or future schemas fail closed; there is no automatic migration or memory fallback.
- Approval accepts `decision`, `expected_version`, and an optional bounded `reason`, only for an `AwaitingApproval` Run at the expected version. State/version update, decision, and audit event commit atomically. Stale/repeated submissions do not append another decision or event.
- There is no semantic Intent deduplication or `Idempotency-Key` contract: submitting the same Intent twice creates independent Runs. Duplicate approval replay is guarded by the version/state contract.
- **Approved != Executed.** The server recomputes risk from trusted catalog policy; model-supplied risk cannot lower it.
- Planning allows one provider attempt. There is no retry, repair call, or fallback. Startup recovery is deterministic and makes zero model/tool calls: it rejects interrupted generation, routes only a committed validated Plan, and preserves other states.
- The local Ollama configuration, approval API, legacy preview, and server-authoritative UI retain their current contracts unless a later stage explicitly revises them.

## 4. Reliability Evidence

The archived final run records **686 passed, 0 failed, 0 errors, 0 skipped, and 5 opt-in live cases deselected**. This is deterministic local evidence, not a production reliability or HA claim.

- **Parsing and proposal trust:** rejects malformed/ambiguous JSON, schema violations, semantic/catalog mismatches, invalid targets or revisions, and risk downgrades. Plans are revalidated before persistence and when read back.
- **Provider and Run failure handling:** bounded transport/envelope and response sizes; timeout, connection, JSON, schema, and semantic failures produce one safe durable rejection. No raw model output is stored as trusted Plan data; no retry occurs.
- **Persistence, CAS, and approval atomicity:** file-backed SQLite close/reopen and schema/constraint checks; stale and invalid transitions; rollback at Run/decision/audit/commit boundaries; uncertain commit returns a safe error without replay.
- **Concurrency and idempotency:** independent SQLite connections race same and opposite decisions; one winner commits, losers do not add history. Repeated requests and stale versions are checked for no extra mutation. Bounded lock tests verify safe failure and unchanged rows.
- **Recovery and corruption:** killed subprocesses restart against the same database; 13 scenarios were repeated across 39 fresh recovery processes. Three recovery passes converge without changing business rows again. Corrupt Plans, risk/history, invalid database files, and unsupported future schemas fail closed without reset or repair.
- **UI and browser:** 39 Run frontend cases use controlled HTTP responses; 13 Run browser E2E cases use real HTTP, RunService, validation, and temporary SQLite without response interception; 25 legacy frontend/browser cases cover the retained preview. The 77 recorded browser cases include inert rendering of hostile strings, decision locking, refresh behavior, keyboard/focus and status checks. This is not a cross-browser matrix or full WCAG certification.
- **Packaging and resources:** the recorded freeze passed Ruff, format, lock, wheel/sdist, isolated install, package-resource, schema-byte-identity, secret, and artifact checks. These checks do not establish cloud deployment or production operations.

The migration record also retains three separate one-call local Ollama smoke successes after the JSON-mode repair. They are bounded observations, not long-duration model stability evidence; the final freeze made no live model calls.

## 5. Reusable Core Assets

- **Model boundary and planning:** reuse `ModelProvider`, `PlanningService`, strict parsing, schema/semantic validation, and safe provider error mapping. Add a provider adapter only when a new runtime is explicitly in scope; do not let model output become authority.
- **Run lifecycle and gates:** reuse `Run`, `RunService`, immutable snapshots, CAS/version checks, the approval boundary, and trusted risk recomputation. Extend a reviewed state/contract deliberately; do not bypass the server-owned state or treat approval as execution.
- **Persistence and recovery:** reuse the repository port and named transactional operations. SQLite is proven for the current local single-node scope. Reuse the recovery invariants and evidence patterns; a different storage/ownership model needs an explicit contract and validation.
- **Verification harness:** reuse controlled providers, temporary SQLite, real-backend browser E2E, kill/restart fixtures, failure injection, and package resource checks. Keep mocked frontend tests clearly distinct from real backend browser evidence.
- **Evidence conventions:** keep deterministic, live-local, browser, packaging, and future cloud evidence separate; preserve failures and state the tested boundary with each PASS.

## 6. Intentionally Missing Capabilities

These are deferred boundaries, not Day 2 defects.

| Capability | Classification | Day 2 boundary |
| --- | --- | --- |
| Tool execution, execution worker, Action ledger, MCP execution | FUTURE_STAGE | No executor, Action state, or callable catalog is present; approval cannot cause an effect. |
| Authenticated authorization, identity, RBAC | INTENTIONALLY_DEFERRED | `LOCAL_USER` is only a server marker; the local approval is not authenticated consent. |
| Cloud LLM runtime | FUTURE_STAGE | Day 2 is configured for local Ollama; the repository roadmap places cloud runtime in later work. |
| Production HA and distributed coordination | FUTURE_STAGE | One application owner per local SQLite database; no distributed lease or multi-node proof. |
| Retry/fallback engine | NOT_REQUIRED | Explicitly excluded by the one-attempt and no-silent-replay contracts. |
| Production observability stack | FUTURE_STAGE | No production metrics/logging deployment or operational alerting evidence. |
| Cross-browser matrix and full WCAG certification | FUTURE_STAGE | Recorded browser evidence uses installed headless Edge; accessibility checks are focused, not certification. |

## 7. Remaining Technical Debt

The evidence supports these validation gaps: no cross-browser matrix; no complete WCAG certification; no long-duration Ollama stability run; no production HA, distributed ownership, physical power-loss, or cloud durability proof; and no authenticated approval/authorization. SQLite remains local and single-node by contract. These are known limits for future scope, not evidence of a broken Day 2 acceptance gate. Retry/fallback and execution are deliberate omissions, not reliability fixes to add silently.

## 8. Hackathon Reuse Map

Keep the model-provider boundary, planning/validation path, durable Run lifecycle, server-side risk/approval gates, and repository contract in the core. Intent-driven and recommendation apps can supply their own Intent schemas, domain validation, trusted catalogs, and presentation. Multimodal inputs should be normalized by an adapter into a validated Intent before entering the same control path. External tool adapters require a separately designed Action ledger, authorization, and execution boundary; they cannot be enabled by adding callables to the Day 2 catalog. Competition-specific screens can sit beside the core API without making browser state authoritative. This maps reuse boundaries only; it does not specify an adapter implementation.

## 9. Portfolio / Resume Highlights

- Built a local durable agent Run flow with an explicit eight-state lifecycle and file-backed SQLite persistence.
- Integrated Ollama through a runtime-neutral provider port and enforced strict JSON, schema, semantic, and trusted-risk validation.
- Implemented version-checked local approvals with an atomic state, decision, and audit transaction; stale requests cannot create duplicate decisions.
- Added deterministic, model-free startup recovery and exercised killed-process restarts, repeated convergence, concurrency, and injected SQLite failures.
- Verified a server-authoritative Run console with real-backend browser E2E; final deterministic evidence records 686 passing cases. No production-scale or HA claim is made.

## 10. Day 3 Entry Conditions

- Keep `day2-final-freeze` as the stable rollback point and preserve the frozen state machine.
- Keep SQLite schema v1 stable unless a deliberate, versioned migration is accepted.
- Preserve `Approved != Executed`, server-authoritative state/version/risk, and trusted risk recomputation.
- Preserve the green 686-case deterministic baseline; add capability through the control plane and gates.
- Keep adapters beside or above the reusable core; do not silently add retry or fallback.
- Treat tool execution, an Action ledger, authenticated authorization, and production HA as separate future capabilities with their own contracts and evidence.

The repository does not define a separate Day 3 backlog. Its deferred Stage 1 roadmap requires an entry review before Phase 1 and separate authorization for later work. The most traceable next step is therefore to review that roadmap against this frozen checkpoint and select an explicitly scoped next phase, especially the Turn/Action and authorization boundaries, before implementing execution. No Day 3 implementation is included in this inventory.
