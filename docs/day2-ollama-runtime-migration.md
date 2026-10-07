# Day 2 Prompt 2.5 — local runtime migration

Date: 2026-10-06, Pacific/Auckland. **Foundry Local → Ollama**, for local development stability. This record supersedes runtime setup in Day 1 without rewriting its historical observations. It does not certify host stability or finish the interrupted Prompt 3 UI task.

**Chronology note:** “interrupted Prompt 3” describes the migration-time snapshot. Prompt 3 was subsequently finalized, then Prompt 4 hardening completed at `863a35cd9545371e2ebb006c19114611f63f500f`. The [final freeze](day2-final-freeze.md) records the completed Day 2 checkpoint. The original failed attempt, `PARSE_INVALID_JSON`, unrun downstream live stages and earlier test counts below remain truthful historical evidence; they are not current blockers and are not relabelled PASS.

## Inventory before modification

The clean committed baseline was `ccc950ed47b508d16e72762e12b6d173f5312890`; eight Prompt 3 files were already changed/untracked. Status, full scoped diffs, source/tests/docs and repository-wide runtime searches were inspected before migration edits. There was no `scripts/` directory. The inventory excluded generated caches, `.git`, virtual environments and build artifacts, not repository source/history.

| Classification | Initial occurrences | Disposition |
| --- | --- | --- |
| A — active runtime | `.env.example`, `adapters/configuration.py`, `adapters/models/local_openai.py`; restart/configuration test setup | Replace environment keys, normalize generic local `/v1` base URL, keep the existing adapter and change only URL construction/connect bound |
| B — active setup | `docs/day1-local-run.md`; current README/local live-test guidance | Use Ollama CLI/model discovery and health check; no Foundry prerequisite |
| C — historical evidence | `docs/evidence/day1-local-model-probe.json`, `day1-backend-verification.json`, `day1-backend-live-model.json`, `day1-backend-http-smoke.json`, `day1-browser-e2e.json`, `day1-hardening-verification.json`, `day1-final-freeze.json` | Preserve actual Foundry/CPU/port/model observations byte-for-byte |
| D — historical architecture/docs | `docs/day1-repository-audit.md`, `docs/day1-architecture-freeze.md`, `docs/adr/ADR-009-model-strategy.md`, `docs/adr/ADR-011-day1-planning-preview.md`, `docs/stage1-{project-charter,local-cloud-boundary,review,implementation-plan}.md` | Preserve historical decisions; this record and the current run guide supersede old runtime setup |

Remaining Foundry names in the current run guide/README describe retirement or explicitly labelled history, not operational instructions. The negative configuration test intentionally proves old environment variables are **not aliases**; it is not an active runtime path. No current production, local setup or default-test path invokes Foundry.

## Configuration and adapter

Required server variables are `LOCAL_LLM_BASE_URL` and `LOCAL_LLM_MODEL`; `.env.example` uses `http://127.0.0.1:11434/v1` and the actually observed `phi4-mini:latest`. The generic URL validator still rejects remote hosts, credentials, queries/fragments, non-HTTP URLs and ambiguous paths. `/v1/` is normalized; a bare loopback origin adds `/v1` once. User request bodies cannot override base URL/model. No old environment alias is retained.

`LocalOpenAIProvider` remains behind the unchanged ModelProvider Protocol and PlanningService. Application/business code has no Ollama imports. The transport remains plain HTTPX `POST /v1/chat/completions`: one nonstreaming invocation, temperature 0, max_tokens 1024, no retries, redirects, proxies, fallback or response repair. The JSON-mode repair adds exactly `"response_format": {"type": "json_object"}` to that real request payload. The frozen `day1-json-v2` planning prompt remains unchanged. The outer model deadline is at most 28 seconds, connect at most 2 seconds, request deadline 30 seconds, envelope cap 64 KiB and candidate cap 16 KiB. Strict envelope extraction, JSON/schema/semantic validation and trusted risk recomputation remain intact. Generation constraints never replace application validation.

Ollama is already running externally. The app never launches the runtime, waits for it at startup, pulls models or chooses CPU/GPU. The new read-only `scripts/Verify-LocalLLM.ps1` bounds CLI and health/model discovery checks to 5 seconds each and uses only loopback URLs. It never performs inference.

Ollama's [official local OpenAI compatibility documentation](https://docs.ollama.com/api/openai-compatibility) establishes this protocol boundary; the migration uses its local server path, not its cloud examples. No SDK/framework/dependency or lockfile change was required.

## Actual environment and initial failed attempt (history)

- `ollama --version`: **0.35.1**.
- `ollama list`: **phi4-mini:latest**, model ID **78fad5d182a7**, **2.5 GB**.
- GET `/`: 200 / `Ollama is running`; GET `/v1/models`: exact installed `phi4-mini:latest`.
- Setup verification script: PASS. No runtime start, pull, fallback or device tuning.
- Exactly one real generic provider request returned an envelope/content, but the unchanged strict parser rejected the candidate with **PARSE_INVALID_JSON** after **20.197 seconds**. The record contains only configuration, parameters and safe validation summary, not raw output or full prompts.
- The `/api/plans` and `/api/runs` live stages were **NOT_RUN** because the preceding direct structured-plan smoke failed. No automatic or manual retry was performed. The local response is not a successful validated-Plan migration gate.

Those results describe the initial migration attempt, not the later repair. Its failed live record and 600-pass/1-failure deterministic result remain in the evidence history.

## Narrow JSON-mode repair and new live observations

The independently confirmed configuration defect was the absence of an explicit JSON-output request: the prompt alone did not constrain the first Ollama response. The repair is one payload field, not permissive extraction, Markdown stripping, response repair, retries, timeout extensions or fallback. The production parser, schemas, semantics and trusted risk policy are unchanged. Deterministic tests still reject prose, fenced JSON, trailing prose, multiple values and malformed JSON; JSON-mode outputs also remain subject to schema and risk-mismatch rejection. Both public Intent endpoints reject request-level base URL, model and `response_format` overrides before model invocation.

Before any repair live inference, **160 targeted deterministic tests passed**, covering provider/configuration/parser/JSON-mode and the oversized-input browser fixture. Ruff check and format check also passed. The tests include a controlled-provider verification of the live-smoke harness; that is explicitly not live-model evidence.

New observations with Ollama **0.35.1**, exact tag **phi4-mini:latest**, base **http://127.0.0.1:11434/v1**:

| Repair stage | HTTP | Elapsed seconds | Model calls | Strict JSON / schema / semantics / trusted risk |
| --- | --- | --- | --- | --- |
| Direct provider | upstream completion accepted | 11.031 | 1 | PASS / PASS / PASS / PASS |
| Real `/api/plans` | 200, PlanValidated | 6.547 | 1 | PASS / PASS / PASS / PASS |
| Real `/api/runs` | 201, Ready v4 | 7.878 | 1 | PASS / PASS / PASS / PASS |

The Run ID was `676f77b3-f435-4edb-ad73-34e91c5b4107`, risk **READ_ONLY**, `approval_required=false`. Isolated real SQLite audit records confirmed `IntentReceived → PlanGenerating → PlanValidated → Ready`; rereading the persisted Run matched the HTTP response. Approval decisions and tool invocations were zero. The smoke server stopped and its private SQLite directory was removed; this temporary Run is not a persistent user Run to look up later.

Exactly three real model calls were made during this repair, one per authorized stage, with no retries. Upstream observation recorded the actual JSON-mode field and model, never the prompt or full raw output. Direct evidence is under `.runtime/ollama-repair/direct`; API evidence is `.runtime/ollama-repair/api/api-smoke.json`. Default tests remain deterministic and the five real-model/browser/study tests remain opt-in. No optional live browser cohort was run.

Full deterministic regression passed **621 tests**, **0 failures**, **0 errors**, **0 skipped**, with **5 live tests deselected**, in **297.19 seconds**. Exact split: Day 1 **191**, Day 2 Prompt 2 **340**, Prompt 3 frontend **39**, Prompt 3 real-backend browser E2E **12**, migration/repair **39** (19 existing migration + 20 new repair tests). The 76 total browser cases, including Day 1, passed; all 76 servers/ports, four browser/driver contexts and 12 temporary Run database directories were cleaned up.

Ruff check, Ruff format check (91 files), `git diff --check`, lock verification and offline wheel/sdist build passed. A new isolated environment outside the repository installed 22 hash-pinned runtime dependencies plus the repaired wheel, with PYTHONPATH unset. Existing package verification passed static/Plan/Run/approval/reopen checks using three controlled-provider calls and zero live calls. Source/wheel/sdist verification found 24 runtime resources and four schemas byte-identical. The production planning prompt AST and 14 pre-existing files outside the repair were unchanged. Remaining runtime-name search results are classified as historical documents/evidence, migration notes or a negative retired-variable test; unexpected active runtime dependencies are zero.

**DAY2_PROMPT2_5_OLLAMA_MIGRATION=PASS** for this narrow local repair. These successful bounded local observations do not measure runtime/host stability or production reliability.

## Preserved work and limits

Prompt 3 static UI, frontend/real-backend E2E modules and their existing edits are preserved. The reason-boundary DOM test had failed before migration because native browser fill truncated 1025 emoji to 1024 Unicode characters. The production field actually has `maxlength=2048`, measured in UTF-16 units, and application validation allows at most 1024 Unicode characters / 4096 UTF-8 bytes. Only the test fixture changed: browser JavaScript directly assigns 1025 emoji, dispatches a bubbling `input` event, and asserts **1025 Unicode characters / 2050 UTF-16 units** before clicking Approve. It now verifies INVALID_APPROVAL, unchanged v4, and no approval request. Production UI bytes, maxlength and validation are unchanged.

Run state/version, SQLite schema/persistence, local approval/CAS/audit, startup recovery, public HTTP contracts, trusted catalog and schemas are not redesigned. No tools execute. No authentication, cloud provider, agent framework, model retry or fallback is introduced. Original Foundry evidence remains history, not relabelled Ollama evidence. No commit, push or tag is part of this task.

Exact regression, package, preservation and quality results are in [migration evidence](evidence/day2-ollama-migration.json). A failed live structured-output check must not be reported as PASS; performance/production reliability and the interrupted Prompt 3 acceptance gate remain unverified.
