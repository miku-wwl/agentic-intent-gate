# Day 1 backend: local run and validation

Prompt 2 implements only the stateless planning backend. The frozen [Intent](../schemas/day1-intent.schema.json), [Plan](../schemas/day1-plan.schema.json), [state/error contract](day1-architecture-freeze.md), and [ADR-011](adr/ADR-011-day1-planning-preview.md) remain unchanged. Proposals never execute tools. UI belongs to Prompt 3.

## Install and discover the existing model

Use Python 3.13 and `uv`; the checked-in `uv.lock` pins product and test dependencies. In PowerShell, from the repository:

```powershell
$env:UV_CACHE_DIR = Join-Path $PWD '.cache\uv'
$env:UV_PYTHON_DOWNLOADS = 'never'
$env:TEMP = Join-Path $PWD '.cache\tmp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null
uv sync --frozen --python 'D:\anaconda3\python.exe'
foundry server status --output json
foundry model list --loaded --search phi-4-mini --output json
```

These Foundry commands inspect the existing daemon. The application does not start/restart it, load/download models, or choose another model. An unavailable service fails visibly. Adjust the Python path to an existing Python 3.13 installation when using another host; all paths above are local workspace caches.

At verification time the existing endpoint was `http://127.0.0.1:39839`. Confirm the **current** endpoint and actual wire ID; the port can change, and the catalog ID with `:5` is not the wire ID:

```powershell
$env:FOUNDRY_LOCAL_ENDPOINT = 'http://127.0.0.1:39839'
Invoke-RestMethod "$env:FOUNDRY_LOCAL_ENDPOINT/v1/models"
$env:FOUNDRY_LOCAL_MODEL = 'Phi-4-mini-instruct-generic-gpu'
$env:MODEL_TIMEOUT_SECONDS = '28'
$env:API_HOST = '127.0.0.1'
$env:API_PORT = '8000'
.venv\Scripts\aig-plans.exe
```

The equivalent entrypoint is `.venv\Scripts\python.exe -m agentic_intent_gate.main`. Export variables explicitly; `.env.example` is documentation, not an automatically loaded file. Missing or invalid configuration exits with a fixed configuration error before serving. Endpoint and bind address must be loopback; no credentials, redirect/proxy traversal, cloud fallback, or request-level configuration overrides are accepted.

## Send a real request

From a second terminal:

```powershell
$body = @{ text = 'Plan to read the service-demo record to investigate its health. Propose only the read step.' } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/api/plans' -ContentType 'application/json; charset=utf-8' -Body $body
```

The only application route is `POST /api/plans`. A success is HTTP 200 with exactly `{ "state": "PlanValidated", "plan": { ... }, "error": null }`. Every rejection has `plan: null`; input rejected before acceptance has `state: null`, and generated/provider failures have `state: "PlanRejected"`.

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
.venv\Scripts\ruff.exe check src tests
.venv\Scripts\ruff.exe format --check src tests
.venv\Scripts\python.exe -m pytest -q
```

The default suite uses scripted providers and mock HTTP transports exclusively for deterministic tests. These cover valid/invalid contracts, unsafe output, controlled error mapping, immutability and total deadlines; they provide no proof of live model quality. No type checker is configured.

With the real endpoint/model variables exported, explicitly enable the separate live lane:

```powershell
$env:LIVE_MODEL_EVIDENCE_DIR = '.runtime\live-model'
.venv\Scripts\python.exe -m pytest -q -m live
```

This sends a real provider request, saves bounded raw output and independent validation results in a uniquely named evidence file, and fails on timeout, unavailability or invalid output. It never skips or substitutes a mock. Running it again is a manual operator decision; the backend never retries automatically.

## Verification evidence and limits

Prompt 2 verification is **PASS**: [backend checks and file manifest](evidence/day1-backend-verification.json) record 141 deterministic tests, linter/formatter checks, pinned dependencies, packaged-schema checks and preserved research hashes. [Real model evidence](evidence/day1-backend-live-model.json) preserves all three manual attempts: two 28-second timeouts with prompt v1, then a fully valid phi-4-mini response with prompt v2 in 10.851 seconds. V2 adds an explicit Plan-instance instruction and a short read example; schema, validation and budgets were unchanged. This is one observed success, not a measured success rate.

[Real HTTP smoke evidence](evidence/day1-backend-http-smoke.json) records actual process startup, an empty Intent returning 400, and a live-model `POST /api/plans` returning 200 / PlanValidated in 11.399 seconds. The smoke process was terminated after checks and its port released; Foundry PID 2260 remained ready. The original [Prompt 1 local probe](evidence/day1-local-model-probe.json) and [architecture-only checks](evidence/day1-quality-check.json) remain historical evidence and are not relabeled as backend success.

Only `service-demo` exists in the trusted preview context; maintenance revision `1` is synthetic. Validation proves the proposal contract, not intent fidelity, live health, authorization, feasibility or execution. Real model output can fail the contract even at temperature zero. There is no approval/execution, persistence, authentication, cloud adapter, UI, or browser test in this slice.

Stop the app with Ctrl+C and verify its port is released; preserve the pre-existing Foundry daemon. Temporary test evidence/logs, caches, `.venv`, and `dist` are ignored. No commit or push is part of Prompt 2.
