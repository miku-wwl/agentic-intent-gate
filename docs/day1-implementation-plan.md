# Day 1 — next prompt implementation plan

**PLAN ONLY**, 2026-10-03, Pacific/Auckland. Implement the [frozen preview](day1-architecture-freeze.md) on the next explicit implementation prompt. No application files below exist today. Do not start the deferred 13-phase reliability/cloud roadmap as part of this slice.

**Prompt 2 scope/status:** the backend paths below are implemented; `adapters/configuration.py` and `tests/test_configuration.py` hold startup configuration and its checks. The historical table/order below includes the later display page: Prompt 2 explicitly defers all static/UI/browser work to Prompt 3, and its acceptance ends at a real live-model `POST /api/plans`. Current setup and evidence are in [day1-local-run.md](day1-local-run.md). Original schemas and deferred reliability scope remain unchanged.

## Exact files and minimal dependencies

Retain the already planned `src/agentic_intent_gate/` package. Create only the following paths and necessary `__init__.py` markers:

| Path to create / modify | Responsibility |
| --- | --- |
| pyproject.toml, uv.lock, .python-version, .gitignore | Python 3.13 baseline, src package, pinned dependency resolution; ignore .venv/.env/cache/runtime; no secrets. Resolve fresh product dependencies, not the reference lockfile |
| .env.example | Required loopback endpoint and actual model wire ID; synthetic service-demo context; no credential |
| src/agentic_intent_gate/domain/contracts.py | Frozen Intent/Plan/PlanStep/Risk/Tool values, preview state and bounded errors; reference deferred Approval/ExecutionResult contracts without instantiating them |
| src/agentic_intent_gate/domain/validation.py | Strict JSON/request parsing, checked-in schema loading, semantic catalog/cap/target/risk/revision checks; construct Plan only on success |
| src/agentic_intent_gate/ports/model_provider.py | ModelProvider Protocol and classified exception; no HTTP/vendor DTOs |
| src/agentic_intent_gate/adapters/tool_catalog.py | Three immutable version-1 descriptions/input schemas/risk mappings and synthetic preview context; no callables or provider I/O |
| src/agentic_intent_gate/adapters/models/local_openai.py | One bounded local HTTP chat request, envelope extraction and error mapping; no provider lifecycle/retries/redirects/proxy use |
| src/agentic_intent_gate/application/generate_plan.py | Transient state sequence, monotonic total timeout, provider invocation, independent validation; no database or Action |
| src/agentic_intent_gate/adapters/local_http.py | POST /api/plans and same-origin GET / static response; explicit frozen HTTP/error shapes |
| src/agentic_intent_gate/main.py | Config validation, composition root, loopback entrypoint; schema path resolved independently of cwd |
| src/agentic_intent_gate/adapters/static/index.html | Small text form + fetch + plan/error display; escaped text only, no approval/execute button |
| tests/conftest.py | Scripted provider and HTTP mock transport fixtures, trusted catalog/context; isolated and visibly test-only |
| tests/test_plan_validation.py | Strict parsing, per-tool schemas, cross-field rules, enum and cap negatives |
| tests/test_model_provider.py | Actual HTTP request/response mapping against controlled transport; failures and total timeout/no retry |
| tests/test_plans_api.py | Route → coordinator → scripted provider → real validator → HTTP contract, including zero calls on bad input |
| tests/test_local_model.py | Explicit opt-in real endpoint/API integration, unavailable failures remain visible; no fake substitution |
| docs/day1-local-run.md, README.md | Reproducible install/run/test commands, fresh host discovery, lane labels, cleanup and current evidence |

Reuse `schemas/day1-intent.schema.json` and `schemas/day1-plan.schema.json` without generating competing models. Use one small HTTP layer (FastAPI/uvicorn), httpx for local model transport, and jsonschema Draft 2020-12 for the canonical schema; pytest/pytest-asyncio are test-only. No Strands, OpenAI SDK, frontend/node toolchain, ORM, database, Docker, cloud SDK, or orchestration framework in this slice. Today no dependency is installed or pinned. Pin compatible current versions when implementing; package/load the two canonical schema resources explicitly and check startup from a different cwd. Core domain values do not require FastAPI/Pydantic imports.

## Order and timebox

1. **Contracts/configuration (about 30 minutes):** build immutable values, strict decoder and catalog from the checked-in schemas. Validate required loopback endpoint and discover/confirm the wire ID. Do not change the existing daemon/models. Test invalid input before wiring any provider.
2. **Provider and validation (about 60 minutes):** compose schema-bearing prompt and one nonstreaming httpx call; map failures; add deterministic structural and semantic negatives. Keep model response text separate from validated Plan. Missing native JSON mode is acceptable; invalid output is a visible rejection.
3. **API (about 45 minutes):** wire POST /api/plans to the coordinator. Consume bounded request bytes before JSON parsing; content/type errors precede IntentReceived. Enforce frozen response/error codes and deadlines. Defaults never invoke paid providers.
4. **Display and evidence (about 30–45 minutes):** serve one static page from the same process, render success/rejection safely, and exercise it against the actual API. Record the real local-model lane separately from deterministic API tests. Update current status without claiming approval/execution.

The first runnable milestone is the API. UI uses the same response contract, not another backend. If the live model rejects a request, the page must show that rejection; do not repair behind the validation boundary or relabel a fake-provider pass as real inference.

## Test strategy and acceptance

| Lane | Required behavior / evidence |
| --- | --- |
| Deterministic contract negatives | Duplicate keys at every nesting, NaN/Infinity/floats, null/scalar/fences/trailing JSON, missing/extra fields, empty/whitespace text, character/UTF-8 byte limits, empty/excess steps, unknown/versioned tool, malformed tool arguments, boolean revision, target mismatch/unknown target, risk downgrade/mismatch, read/mutation limits, duplicate mutation, read after mutation, stale synthetic revision |
| Provider transport | Exact wire ID/path and separate system/user messages, nonstreaming/no function tools, finite token/time/byte caps, redirect/proxy denial, refusal/empty choices/non-stop/tool_calls/envelope failures, refused connection/upstream statuses, slow response TOTAL timeout, one call only, sanitized errors |
| API integration | Bad requests have null state/plan and zero model calls; valid full route returns PlanValidated; invalid output returns 422 and plan null; provider failures give frozen 502/503/504; no arbitrary body provider/target/role overrides; no tool/approval/execution dependency exists |
| Live local model (opt-in) | Fresh CLI status/models plus installed dependency versions and POST through the actual /api/plans route to phi-4-mini. At least one real schema+semantic-valid read proposal, and controlled invalid-output test remains independent. Archive latency/model/wire path and bounded response. If no real valid proposal is observed, report live acceptance FAIL/PARTIAL, not PASS |
| Browser | Actual browser visit to local page → text submission → backend → real local provider → validated plan display, plus rejected-plan and connection-error display. Check literal rendering of HTML-like strings and stale Plan clearing. Use available browser automation; do not install a browser framework solely for a single initial smoke check. Missing browser verification stays NOT VERIFIED |
| Delivery integrity | Confirm only scoped files changed; research/handoff unchanged; no credential, cloud call, database, commit, or push |

Run the deterministic suite without a live service; mock transport proves adapter behavior, not real model quality. The opt-in live lane must fail visibly if enabled with an unavailable service, rather than skipping/falling back. A live provider valid response plus an actual browser flow is required to claim the complete first vertical slice. Unit/schema tests alone never justify E2E PASS.

## Error behavior and deferred work

Use the freeze's exact HTTP table. Each invalid model output or provider failure returns PlanRejected and plan null. No automatic retries, repair call, partial acceptance, persisted preview, or execution. Do not promise risk/goal interpretation correctness merely because deterministic checks pass. Record a new bounded prompt decision if local valid-output frequency needs improvement; weakening enum/policy validation is not a remedy.

Deferred: durable Intent/event identity/Turn admission, all StateStore work, read/mutation adapters, full tool capability registry, proposal hashes, approvals, authorization, execution/result states, reconciliation, delivery, Strands agent integration, cloud/MCP/sandbox, memory, multi-agent work, distributed components and production exposure. The Stage 1 roadmap remains authoritative when those slices are separately authorized.
