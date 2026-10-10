# Development

## Environment

Use Python 3.13, uv, and PowerShell from the repository root:

~~~powershell
uv sync --locked
~~~

This installs the existing FastAPI/HTTPX/jsonschema/Uvicorn runtime and the pytest,
Ruff, and Playwright development group. Do not add dependencies for documentation work.
The packaged UI is `src/agentic_intent_gate/adapters/static/index.html`; no Node install,
`frontend/` build, or separate frontend server is required.

## Local LLM

The implemented provider uses a local OpenAI-compatible HTTP API. Required settings:

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

Only HTTP loopback URLs with explicit ports are accepted; `/v1` is normalized.
`MODEL_TIMEOUT_SECONDS` defaults to 28 and cannot exceed 28. Requests use one
non-streaming JSON completion, bounded output, no retries/repair, and no cloud fallback.
The runtime does not set Ollama keep-alive, CPU/GPU selection, or model residency.
Do not claim model quality or successful inference from a healthy model-list endpoint.

## Default planning/Run application

~~~powershell
$env:API_HOST = '127.0.0.1'
$env:API_PORT = '8000'
$env:AIG_DB_PATH = '.runtime/day2-runs.sqlite3'
uv run aig-plans
~~~

Open `http://127.0.0.1:8000/`. This v1 entry point supports preview, persistent Runs,
approval/rejection, and Run recovery, not Action execution. The environment is read
directly; `.env.example` is not loaded automatically. Use local file-backed SQLite, not
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

from agentic_intent_gate.adapters.local_capability import provision_capability
from agentic_intent_gate.adapters.service_demo import provision_demo_store
from agentic_intent_gate.adapters.sqlite_v2_runs import SQLiteV2RunRepository
from agentic_intent_gate.adapters.tool_catalog import build_validator

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
uv run pytest -q tests/test_sqlite_run_repository.py
uv run pytest -q tests/test_dummy_adapter_contract.py tests/test_dummy_adapter_e2e.py
uv run pytest -q tests/test_service_demo_adapter.py tests/test_action_execution.py tests/test_execution_hardening.py
~~~

The SQLite v1 canonical fixture is `tests/fixtures/sqlite_run_v1.sql`; preserve its exact
schema invariant and the real database structure/pragma checks.

Adapter tests run the Dummy directly with test-owned real SQLite stores; they are not
production Action registration tests. Use nearby persistence, capability, and compiler
tests when changing those boundaries.

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
