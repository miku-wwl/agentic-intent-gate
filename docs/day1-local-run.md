# Day 1 local planning preview: run and validation

Prompt 2 implements the stateless planning backend; Prompt 3 adds the same-origin static page. The frozen [Intent](../schemas/day1-intent.schema.json), [Plan](../schemas/day1-plan.schema.json), [state/error contract](day1-architecture-freeze.md), and [ADR-011](adr/ADR-011-day1-planning-preview.md) remain unchanged. Proposals never execute tools.

## Install and discover the existing model

Use Python 3.13 and `uv`; the checked-in `uv.lock` pins product and test dependencies. In PowerShell, from the repository:

```powershell
$env:UV_CACHE_DIR = Join-Path $PWD '.cache\uv'
$env:UV_PYTHON_DOWNLOADS = 'never'
$env:TEMP = Join-Path $PWD '.cache\tmp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null
uv sync --frozen --python 3.13
foundry server status --output json
foundry model list --loaded --search phi-4-mini --output json
```

These Foundry commands inspect the existing daemon. If it is stopped, explicitly run `foundry server start --output json`; to load your already downloaded phi-4-mini, run `foundry model load phi-4-mini --output json`. The application does not manage the daemon/models. An unavailable service fails visibly. Python 3.13 must already be installed and discoverable by `uv`; all paths above are local workspace caches.

At Prompt 3 verification time the existing endpoint was `http://127.0.0.1:59656`, with phi-4-mini's CPU variant loaded. Prompt 2's earlier GPU/port observations remain historical. Confirm the **current** endpoint and actual wire ID; the port/variant can change, and the catalog ID with `:5` is not the wire ID:

```powershell
$status = foundry server status --output json | ConvertFrom-Json
if (-not $status.running -or -not $status.webUrls) { throw 'Foundry is unavailable' }
$env:FOUNDRY_LOCAL_ENDPOINT = @($status.webUrls)[0]
$models = Invoke-RestMethod "$env:FOUNDRY_LOCAL_ENDPOINT/v1/models"
$loaded = @($models.data | Where-Object { $_.parent -eq 'phi-4-mini' })
if ($loaded.Count -ne 1) { throw 'Select the loaded phi-4-mini wire ID explicitly' }
$env:FOUNDRY_LOCAL_MODEL = $loaded[0].id
$env:MODEL_TIMEOUT_SECONDS = '28'
$env:API_HOST = '127.0.0.1'
$env:API_PORT = '8000'
.venv\Scripts\aig-plans.exe
```

The equivalent entrypoint is `.venv\Scripts\python.exe -m agentic_intent_gate.main`. Export variables explicitly; `.env.example` is documentation, not an automatically loaded file. Missing or invalid configuration exits with a fixed configuration error before serving. Endpoint and bind address must be loopback; no credentials, redirect/proxy traversal, cloud fallback, or request-level configuration overrides are accepted.

## Send a real request

Open **http://127.0.0.1:8000/** in your browser. Enter `Plan to read the service-demo record.` and click **Generate Plan**, or press Ctrl+Enter in the textarea. The page shows loading, then the validated goal, target, ordered tools/arguments and risk, or a safe error. Each click makes at most one same-origin request. An empty intent stays in the browser; the backend remains authoritative. A new request clears old results/errors and disables duplicate submission until it completes.

From a second terminal:

```powershell
$body = @{ text = 'Plan to read the service-demo record to investigate its health. Propose only the read step.' } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/api/plans' -ContentType 'application/json; charset=utf-8' -Body $body
```

`GET /` serves the static UI and `POST /api/plans` retains its existing contract. A success is HTTP 200 with exactly `{ "state": "PlanValidated", "plan": { ... }, "error": null }`. Every rejection has `plan: null`; input rejected before acceptance has `state: null`, and generated/provider failures have `state: "PlanRejected"`.

| HTTP | Error code | Meaning |
| --- | --- | --- |
| 400 | INVALID_INTENT | Strict request parsing, Intent schema, text bounds or request-read timeout |
| 413 | REQUEST_TOO_LARGE | More than 16 KiB request bytes |
| 415 | UNSUPPORTED_MEDIA_TYPE | Require application/json and UTF-8 |
| 422 | PLAN_VALIDATION_ERROR | Candidate failed parsing, schema or semantic rules |
| 502 | MODEL_RESPONSE_ERROR | Unexpected status or invalid/partial completion envelope |
| 503 | MODEL_UNAVAILABLE | Connection/model unavailable, upstream 429/5xx |
| 504 | MODEL_TIMEOUT | Total model/application deadline exceeded |
| 500 | INTERNAL_ERROR | Sanitized unexpected application failure |

Errors use a fixed message and at most ten `{path, code}` issues, with no values, prompts, upstream bodies or stack traces. `PARSE_*`, `SCHEMA_*`, and `SEMANTIC_*` issue codes distinguish invalid candidate phases. Risk is computed from trusted tool definitions and must exactly match the claim. Tools and versions, target equality, proposal caps/order, duplicate mutations, byte bounds and synthetic revision are checked before constructing an immutable Plan.

Provider I/O has a total budget of at most 28 seconds; the application budget is 30 seconds including validation, with two seconds reserved before returning from the model. HTTPX's per-operation timeouts are supplemented by monotonic asyncio deadlines because they do not provide a total-call budget ([HTTPX timeout documentation](https://www.python-httpx.org/advanced/timeouts/)). There is one nonstreaming inference attempt, no repair or retry. Timeout does not prove Foundry stopped inference; it creates no external tool effect.

## Test lanes

```powershell
.venv\Scripts\ruff.exe check .
.venv\Scripts\ruff.exe format --check .
.venv\Scripts\python.exe -m pytest -q
```

The Prompt 3 baseline contained 166 deterministic tests. Prompt 4 adds only missing regression boundaries and a controlled 20-request concurrency check; the current total is recorded in the [hardening evidence](evidence/day1-hardening-verification.json). UI tests use Python Playwright with the installed Microsoft Edge (`msedge`) channel; there is no browser download, npm pipeline or frontend server. Set `AIG_BROWSER_CHANNEL=chrome` to use an installed Chrome instead. Playwright supports these installed channels ([official browser documentation](https://playwright.dev/python/docs/browsers)). Browser tests use isolated contexts and workspace temporary profiles, then close their browsers and HTTP servers. No type checker is configured.

To run only backend tests, use `pytest -m "not browser and not live"`; to run deterministic browser tests, use `pytest tests/browser -m "browser and not live"`. Both use `.venv\Scripts\python.exe -m pytest` as above. The Playwright fixture closes per module so its synchronous event loop does not remain active during pytest-asyncio backend tests. Controlled responses/providers are deterministic evidence, not live model proof.

With the real endpoint/model variables exported, explicitly enable the separate live lane:

```powershell
$env:LIVE_MODEL_EVIDENCE_DIR = '.runtime\live-model'
.venv\Scripts\python.exe -m pytest -q tests/test_local_model.py -m live
```

This sends a real provider request, saves bounded raw output and independent validation results in a uniquely named evidence file, and fails on timeout, unavailability or invalid output. It never skips or substitutes a mock. Running it again is a manual operator decision; the backend never retries automatically.

For the separate real browser lane:

```powershell
.venv\Scripts\python.exe -m pytest -q tests/browser/test_live_browser.py -m live_browser
```

This launches the actual application entrypoint on an available loopback port, opens Edge, enters the intent, clicks the button, observes the real HTTP response and checks every rendered Plan field. It performs no browser interception or provider substitution. Screenshots, timings, requests/responses and cleanup records go to a uniquely named `.runtime/browser-evidence/` directory. Failure remains visible; any repeat is manual. The test stops its own app and browser and verifies port release, while preserving Foundry.

## Verification evidence and limits

Prompt 4's [hardening evidence](evidence/day1-hardening-verification.json) records the frozen 12-attempt sequential model matrix, three live injection attempts, two five-attempt real browser cohorts and one diagnostic request, deterministic regressions and package verification. The earlier browser cohort's five compatibility failures remain recorded separately from the final cohort. Reproduce the fixed study only with explicit live configuration:

```powershell
$env:HARDENING_EVIDENCE_DIR = '.runtime\hardening'
.venv\Scripts\python.exe -m pytest -q -s tests/test_live_repeatability.py -m live
.venv\Scripts\python.exe -m pytest -q -s tests/browser/test_live_browser_repeatability.py -m live_browser
```

Each command uses a fixed schedule independent of outcomes, with one inference call per attempt. Timeouts/rejections remain observations; they never trigger retries. The study checks at least one successful result and all observed failures for safe handling, rather than requiring 100% success. Raw bounded model observations stay in ignored runtime files. The normal study uses the real network provider and actual API via ASGI; the browser study uses the real product process. Neither substitutes a provider or intercepts browser traffic. Alignment classification is study-only, not a new product validator.

For packaging, run `uv build`, then create a clean environment **outside** this repository with `uv venv --python 3.13 <outside-directory>`. Export pinned runtime dependencies with `uv export --frozen --no-dev --no-emit-project --format requirements-txt --output-file .runtime/package-dependencies.txt`; install them with `uv pip install --python <clean-python> --require-hashes -r .runtime/package-dependencies.txt`, and install the wheel with `uv pip install --python <clean-python> --no-deps dist/agentic_intent_gate-0.1.0-py3-none-any.whl`. From a working directory outside the repository, invoke `<clean-python> <repository>/tests/package_check.py <repository> <evidence-output>`, with `PYTHONPATH` unset. This checks wheel imports, byte-identical schemas, bundled HTML and GET/POST using a controlled provider; it is PACKAGE evidence, not live inference.

Prompt 3 is **DAY1_LOCAL_E2E=PASS**. [Browser verification evidence](evidence/day1-browser-e2e.json) separates the 166-test deterministic suite (18 frontend cases and six browser/backend cases included) from three manual real-browser/phi-4-mini attempts. The first two CPU-model requests returned safe 504 rejections after about 28 seconds; the third returned 200 / PlanValidated in 26.125 seconds, with all Plan fields rendered in 26.204 seconds. Model, prompt, validation and budgets were unchanged. This is one observed successful flow, not a measured success rate. The [success screenshot](evidence/day1-browser-live-success.png) and [timeout screenshot](evidence/day1-browser-live-rejection.png) show the actual browser states. Test app/browser processes were closed and all test ports released; the existing Foundry PID 13032 remained ready.

Prompt 2 verification is **PASS**: [backend checks and file manifest](evidence/day1-backend-verification.json) record 141 deterministic tests, linter/formatter checks, pinned dependencies, packaged-schema checks and preserved research hashes. [Real model evidence](evidence/day1-backend-live-model.json) preserves all three manual attempts: two 28-second timeouts with prompt v1, then a fully valid phi-4-mini response with prompt v2 in 10.851 seconds. V2 adds an explicit Plan-instance instruction and a short read example; schema, validation and budgets were unchanged. This is one observed success, not a measured success rate.

[Real HTTP smoke evidence](evidence/day1-backend-http-smoke.json) records actual process startup, an empty Intent returning 400, and a live-model `POST /api/plans` returning 200 / PlanValidated in 11.399 seconds. The smoke process was terminated after checks and its port released; Foundry PID 2260 remained ready. The original [Prompt 1 local probe](evidence/day1-local-model-probe.json) and [architecture-only checks](evidence/day1-quality-check.json) remain historical evidence and are not relabeled as backend success.

Only `service-demo` exists in the trusted preview context; maintenance revision `1` is synthetic. Validation proves the proposal contract, not intent fidelity, live health, authorization, feasibility or execution. Real model output can fail the contract even at temperature zero. The page exposes validated proposals and fixed user-facing error messages; raw model output, prompts and exception text remain outside the UI. Approval/execution, persistence, authentication and cloud adapters remain deferred.

Prompt 4 measured 10/12 aligned normal successes (two maintenance timeouts), three safe live injection outcomes, and 3/5 successful renders in the final browser cohort. The earlier five provider-compatibility failures and one diagnostic timeout are retained separately. Reliability is **WEAK**: CPU-model latency remains close to the unchanged deadline, and maintenance requests succeeded only 2/4. No latency optimization was attempted or retained. Production reliability is **NOT VERIFIED**; general intent alignment has not been comprehensively evaluated.

The [Prompt 5 final freeze record](evidence/day1-final-freeze.json) records the repository review, complete deterministic/browser gate and repeated automated package check. Its one real-browser request used `Plan to read the service-demo record.` and returned HTTP 504 / PlanRejected in 28.212 seconds, with `plan: null` and a visible timeout by 28.308 seconds. All Plan fields were empty; a subsequent GET / returned 200. This is a verified safe timeout, not a successful generated Plan or a new reliability estimate. No retry, model/prompt/timeout change or product feature was introduced. Test app/browser cleanup released the port and preserved Foundry.

Stop the app with Ctrl+C and verify its port is released; preserve the pre-existing Foundry daemon. Temporary test evidence/logs, caches, `.venv`, and `dist` are ignored. No commit or push is part of the Prompt 5 freeze review.
