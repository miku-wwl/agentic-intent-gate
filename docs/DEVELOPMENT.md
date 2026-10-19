# Development

## Environment

Use Python 3.13, uv, and PowerShell from the repository root:

~~~powershell
uv sync --locked
~~~

This installs the FastAPI/HTTPX/jsonschema/python-dotenv/Uvicorn runtime and the pytest,
Ruff, and Playwright development group. `python-dotenv` reads the optional `.env` file.
The packaged UI is `src/agentic_intent_gate/api/static/index.html`; no Node install,
`frontend/` build, or separate frontend server is required.

Internal directories use PEP 420 implicit namespace packages: only
`src/agentic_intent_gate/__init__.py` is a package marker. Import from the concrete module,
not package-root re-exports. `api/` owns HTTP/assets, `config/` owns settings, and
`infrastructure/` contains persistence, LLM, security, and tools; Application/Domain/Ports
retain their existing boundaries. UI resources resolve from the top-level package via
`files("agentic_intent_gate").joinpath("api", "static", "index.html")`.

## Model provider configuration

`aig-plans` and the explicit execution demo read `.env` from the current working directory
when it exists. Process environment variables override values in that file. The file is
optional; existing environment-only setups keep working. Start from the commented examples:

~~~powershell
Copy-Item .env.example .env
~~~

Uncomment one provider block, remove other active provider settings, and replace every
model/key placeholder. When `AIG_LLM_PROVIDER` is omitted, the selected provider remains
Ollama. Existing `LOCAL_LLM_BASE_URL` and `LOCAL_LLM_MODEL` settings remain valid.

**Ollama (local default):**

~~~powershell
$env:LOCAL_LLM_BASE_URL = 'http://127.0.0.1:11434/v1'
$env:LOCAL_LLM_MODEL = 'phi4-mini:latest'
ollama list
.\scripts\Verify-LocalLLM.ps1 -BaseUrl $env:LOCAL_LLM_BASE_URL -Model $env:LOCAL_LLM_MODEL
~~~

Use the exact installed model tag. If Ollama is stopped, run `ollama serve` in another
terminal; downloading a missing model is a separate operator decision. The read-only
verification script checks the Ollama CLI, local health, and model listing, not inference.
The provider is protocol-based, not an Ollama SDK integration.

The existing `LOCAL_LLM_*` values select Ollama without needing `AIG_LLM_PROVIDER`. The
new names are `AIG_LLM_PROVIDER=ollama`, `AIG_LLM_MODEL`, and `AIG_LLM_BASE_URL`. Ollama
URLs must be HTTP loopback with an explicit port; `/v1` is normalized.

**OpenAI:**

~~~dotenv
AIG_LLM_PROVIDER=openai
AIG_LLM_MODEL=<supported-openai-model-id>
OPENAI_API_KEY=<OpenAI-secret>
~~~

This uses the fixed `https://api.openai.com/v1` endpoint and OpenAI Chat Completions JSON
mode. The selected model must accept that request format; unsupported model/parameter
responses fail closed without retry.

**Anthropic Claude:**

~~~dotenv
AIG_LLM_PROVIDER=anthropic
AIG_LLM_MODEL=<supported-claude-model-id>
ANTHROPIC_API_KEY=<Anthropic-secret>
~~~

This uses the native Anthropic Messages API at `https://api.anthropic.com/v1`, with the
required `x-api-key` and `anthropic-version` headers. It accepts one completed text block;
refusal, tool-use, truncation, and malformed responses fail closed.

**OpenAI-compatible HTTPS provider:**

~~~dotenv
AIG_LLM_PROVIDER=openai-compatible
AIG_LLM_MODEL=<provider-model-id>
AIG_LLM_BASE_URL=https://<trusted-provider-host>/v1
AIG_LLM_API_KEY=<provider-secret>
~~~

The host is operator-configured, must resolve only to public addresses at configuration
time, and uses HTTPS on port 443. Redirects are disabled. This adapter sends only common
`model`, `messages`, `stream`, and `max_tokens` fields; it deliberately omits `response_format`
because compatible services differ in their structured-output support. The model must follow
the JSON prompt, then AIG's existing strict Plan validator checks its output.

`MODEL_TIMEOUT_SECONDS` defaults to 28 and cannot exceed 28. Cloud and local requests use one
non-streaming attempt, strict timeouts, bounded uncompressed JSON responses, no implicit
retry, no repair, and no provider fallback. A missing cloud key fails before the app serves.
The runtime does not set Ollama keep-alive, CPU/GPU selection, or model residency. Do not
claim model quality or successful inference from configuration or endpoint health alone.

## Default planning/Run application

~~~powershell
$env:API_HOST = '127.0.0.1'
$env:API_PORT = '8000'
$env:AIG_DB_PATH = '.runtime/day2-runs.sqlite3'
uv run aig-plans
~~~

Open `http://127.0.0.1:8000/`. This v1 entry point supports preview, persistent Runs,
approval/rejection, and Run recovery, not Action execution. Settings load optional `.env`
values and then apply process environment overrides. Use local file-backed SQLite, not
memory databases, SQLite URI paths, UNC paths, or mapped network drives.

## Explicit v2 local execution demo

Stop any predecessor process and helpers using the gate/store. The
`--predecessor-stopped` flag is an operator attestation, not a command that stops them.
Use separate **new** private files for this example; do not reuse the default v1 database.
There is an explicit migration implementation for existing v1 data, but startup never
performs that migration silently.

With the LLM settings above still exported:

~~~powershell
$demoDirectory = Join-Path $env:LOCALAPPDATA 'AgenticIntentGate\demo'
New-Item -ItemType Directory -Force -Path $demoDirectory | Out-Null
$env:AIG_DB_PATH = Join-Path $demoDirectory 'gate-v2.sqlite3'
$env:AIG_DEMO_DB_PATH = Join-Path $demoDirectory 'notes.sqlite3'
$env:AIG_CAPABILITY_FILE = Join-Path $demoDirectory 'operator-capability'
$env:AIG_EXECUTION_SCOPE = [guid]::NewGuid().ToString()
$env:AIG_SUBJECT_ID = 'local-operator'
$env:AIG_POLICY_REVISION = 'local-demo-v1'

$provisionDemo = @'
import os
from pathlib import Path

from agentic_intent_gate.infrastructure.security.local_capability import provision_capability
from agentic_intent_gate.infrastructure.tools.service_demo import provision_demo_store
from agentic_intent_gate.infrastructure.persistence.sqlite_v2_runs import SQLiteV2RunRepository
from agentic_intent_gate.infrastructure.tools.tool_catalog import build_validator

paths = [Path(os.environ[key]) for key in
         ("AIG_DB_PATH", "AIG_DEMO_DB_PATH", "AIG_CAPABILITY_FILE")]
if any(path.exists() for path in paths):
    raise SystemExit("Provisioning requires new files; retain existing resources.")
provision_capability(paths[2])
provision_demo_store(paths[1], os.environ["AIG_EXECUTION_SCOPE"])
SQLiteV2RunRepository(paths[0], build_validator())
'@
uv run python -c $provisionDemo
uv run python -m agentic_intent_gate.execution_main --predecessor-stopped
~~~

Protect the directory with current-user access. Capability provisioning creates a private
file (owner-only POSIX mode/current-user Windows ACL); it refuses replacement.
Keep these files and the original scope/subject/policy settings across restarts.
Do not rerun provisioning or generate a fresh scope for an existing demo store.
Restart with the retained environment and only the final startup command.

Privately read the capability file as the local operator and enter its token in the UI's
memory-only capability field. Do not paste it into logs, source, screenshots, or shell
command arguments. The demo workflow is: create a single `append_service_note` Plan,
approve it, admit an Action, authorize it, execute once, and refresh the persisted result.
Only `service-demo` note append is executable; the other catalog tools and Dummy are not.

## Focused tests

Default pytest settings exclude opt-in `live` tests:

~~~powershell
uv run pytest -q tests/test_configuration.py tests/test_local_runtime_configuration.py tests/test_model_provider.py tests/test_cloud_providers.py
uv run pytest -q tests/test_sqlite_run_repository.py
uv run pytest -q tests/test_consumer_runtime.py
uv run pytest -q tests/test_execution_budget.py
uv run pytest -q tests/test_dummy_adapter_contract.py tests/test_dummy_adapter_e2e.py
uv run pytest -q tests/test_service_demo_adapter.py tests/test_action_execution.py tests/test_execution_hardening.py
~~~

The SQLite v1 canonical fixture is `tests/fixtures/sqlite_run_v1.sql`; preserve its exact
schema invariant and the real database structure/pragma checks.

Adapter tests run the Dummy directly with test-owned real SQLite stores; they are not
production Action registration tests. Use nearby persistence, capability, and compiler
tests when changing those boundaries.

## Independent consumer and wheel verification

The supported public integration module is `agentic_intent_gate.runtime`.
See [Adapter guide](ADAPTER_GUIDE.md) and [consumer app](../examples/consumer/app.py).
Use a new private local directory; the example provisions an exclusive capability file,
retains scope/identity, and stores effects separately from the gate. It uses deterministic
planning and scripted reviewed approval for demonstration only. No live LLM is needed.

The shared runtime accepts a trusted external `ModelProvider`, but its bundled
`LocalOpenAIProvider` prompt remains tailored to `service-demo` and
`arguments.service_id`; it is not a general non-demo planner. Consumers needing
model-based planning for another domain must supply their own appropriate provider and
retain the runtime's strict Plan validation. The shipped UI's Action admission control
also remains specific to `append_service_note/1` on `service-demo`. A consumer can admit
through the supported HTTP/API interface or provide its own UI, then use the existing
Runtime authorization and execution capabilities within their documented binding and
approval boundaries. The reference consumer's scripted approval is example behavior,
not production-grade multi-user authentication.

From the checkout, build and prepare an **outside-checkout** installation:

~~~powershell
$consumerRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('aig-consumer-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $consumerRoot | Out-Null
uv build --offline --out-dir (Join-Path $consumerRoot 'dist')
uv venv --python 3.13 (Join-Path $consumerRoot 'venv')
$consumerPython = Join-Path $consumerRoot 'venv\Scripts\python.exe'
$consumerWheel = Join-Path $consumerRoot 'dist\agentic_intent_gate-0.3.0-py3-none-any.whl'
uv pip install --python $consumerPython $consumerWheel
uv pip install --python $consumerPython 'pytest>=8,<10' 'pytest-asyncio>=1,<2'
New-Item -ItemType Directory -Path (Join-Path $consumerRoot 'examples\consumer'), (Join-Path $consumerRoot 'tests') | Out-Null
Copy-Item examples\consumer\app.py (Join-Path $consumerRoot 'examples\consumer\app.py')
Copy-Item tests\test_consumer_runtime.py (Join-Path $consumerRoot 'tests\test_consumer_runtime.py')
Copy-Item tests\test_execution_budget.py (Join-Path $consumerRoot 'tests\test_execution_budget.py')
Push-Location $consumerRoot
try {
    & $consumerPython -I examples\consumer\app.py data --provision --predecessor-stopped
    & $consumerPython -I examples\consumer\app.py data15 --provision --predecessor-stopped --execution-timeout-seconds 15
    & $consumerPython -I -m pytest -q -o asyncio_mode=auto tests\test_consumer_runtime.py tests\test_execution_budget.py
    uv pip check --python $consumerPython
} finally {
    Pop-Location
}
~~~

These commands copy only example/test code, not core implementation. `-I` prevents
source PYTHONPATH injection. A fresh wheel install may need network access for dependencies;
offline builds require cached Hatch dependencies. Do not publish or change package version
just to test installation. This working tree targets package version `0.3.0`; it is not
tagged or published. MIT metadata and the license are included in wheel/sdist. The sdist
uses an explicit source/docs/test allowlist;
ignored local databases, credentials, environments and generated evidence must remain absent.

For Git-based installation, pin the verified **full commit SHA**, never a moving branch.
The [v0.2.0 consumer handoff](V0.2.0-CONSUMER-HANDOFF.md) records the exact pin and verified
annotated tag. Historical `v0.1.0` remains at
`fd742b783058a53400c465b4ab59c6e80715895a`; old consumers may keep that dependency.
Git tags do not imply PyPI publication. Private Git access requires separate authentication.
Never embed credentials in Git URLs, source, or documentation.

The budget tests use fake monotonic clocks and synthetic SQLite effects. They exercise the
real Gateway, approvals, grants, claims, results and recovery; they do not contact providers.
The [Adapter guide](ADAPTER_GUIDE.md) documents executable default/extended examples and the
fresh binding/scope rules. Live Ollama and external side-effect tests are not release checks.

## Browser E2E and complete offline regression

Install Microsoft Edge for the default Playwright `msedge` channel. Another installed
compatible channel can be selected with `AIG_BROWSER_CHANNEL`.

~~~powershell
uv run pytest -q tests/browser
uv run pytest -q
~~~

The first command runs deterministic UI/real-backend browser tests, excluding live tests.
Some frontend tests deliberately control HTTP responses; real-backend tests use bounded
planning providers and actual HTTP/SQLite execution paths. They do not prove live LLM
behavior or production deployment. The second command runs the complete deterministic
offline suite, including browser tests, once for a justified acceptance run.

Browser fixtures manage their HTTP servers and installed browser; a manually started
application is not required. Test evidence/temporary browser files normally go under
ignored `.runtime/`. Keep credentials out of evidence. Live tests remain opt-in; do not
enable them or substitute mocks merely to claim a real-provider result.

## Static and metadata checks

~~~powershell
uv run ruff check src tests
uv run ruff format --check src tests
uv run ruff check examples
uv run ruff format --check examples
uv lock --check --offline
git diff --check
~~~

Use `ruff format` only on files within the task's edit scope. Broad regression is not
required after every isolated adapter edit; select it when crossing shared boundaries or
when explicitly requested. Report exact executed results, not historical counts.

## Troubleshooting and Hackathon iteration

- Configuration rejected: check exported variables, exact model tag, loopback URL/port,
  timeout bound, and local database path.
- Invalid Plan: inspect bounded validation feedback; do not bypass schema or rewrite risk
  to make model output pass.
- v2 startup denied: check private capability, stable store scope, correct v2 schema,
  single-owner lock, and genuine predecessor/helper quiescence. Never delete a ledger
  just to make startup succeed.
- Admission/authorization denied: check exact approval, supported single-step tool,
  capability binding, and expected versions. Do not weaken policies.
- Unknown outcome: inspect durable state and receipts; do not repeat execution blindly.
- Browser launch failure: verify the selected installed channel and local runtime
  permissions; distinguish NOT RUN from a passing browser result.

Follow the [Adapter guide](ADAPTER_GUIDE.md): start with an isolated allowed capability and
deterministic tests, then review explicit runtime integration separately. Keep competition
code removable and avoid unrelated core changes. Read [AGENTS.md](../AGENTS.md) and
[Security](SECURITY.md) before implementing.
