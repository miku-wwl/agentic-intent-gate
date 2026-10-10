# Agentic Intent Gate — A Reliable, Approval-Gated AI Agent Runtime

A local-first agent runtime that turns natural-language requests into strictly validated
Plans, persists their lifecycle, and separates human approval from authorized tool execution.
The shared baseline supports explicitly trusted, removable single-tool Hackathon consumers.
It remains a local development/demo runtime, not a production deployment or plugin framework.

## What works

- Local OpenAI-compatible LLM planning, JSON Schema validation, semantic checks, and a
  deterministic risk gate.
- Durable Runs, human approval/rejection, exact approved-Plan binding in SQLite v2,
  compare-and-swap (CAS), audit events, and fail-closed recovery.
- Explicit local execution composition: capability verification, trusted compilation,
  authorization, an Action ledger, stable effect keys, and one SQLite-backed demo effect.
- A backend-served browser UI for planning, Runs, approval, and optional Action controls.
- An isolated Dummy Adapter reference with real SQLite contract/integration tests.
- An explicit single-binding consumer API, bounded non-demo Plan schemas, frozen registration
  and capability policy, and an independent SQLite-backed audit consumer.

## Current limits

The unchanged demo composition executes only `append_service_note/1` against `service-demo`.
Independent hosts can explicitly register one trusted SIDE_EFFECT tool through
`agentic_intent_gate.runtime`; only one approved step is executable. Planning catalog
entries do not imply executable tools. The Dummy Adapter
is **not** registered there or exposed in the browser. There is no dynamic adapter/plugin
system, MCP integration, multi-agent orchestration, or general-purpose tool execution.
Local bearer capabilities are not production user authentication. Distributed HA,
external-provider security, and production operations are not proven. Unknown outcomes
are not automatically retried or reconciled.

## Architecture

Intent → LLM Plan → schema/semantic validation → deterministic Gate → persistent Run →
human approval → exact binding → trusted Action compilation → authorization → durable
claim → ToolExecutor → persistent outcome.

Approval alone never executes a tool. The default entry point stops at Runs/approval;
the explicitly configured v2 entry point enables the local execution demo.
Source is separated into API, Application, Domain, Ports, Infrastructure, and Configuration
layers. See [Architecture](docs/ARCHITECTURE.md) for the demo, consumer, and isolated Dummy
paths and actual APIs.

## Quick start: planning and approval

Prerequisites: Python 3.13, [uv](https://docs.astral.sh/uv/), and an installed local LLM
server exposing the OpenAI-compatible API. Core dependencies are FastAPI, HTTPX,
jsonschema, and Uvicorn; development tools include pytest, Ruff, and Playwright.

From the repository root in PowerShell:

~~~powershell
uv sync --locked
ollama list
$env:LOCAL_LLM_BASE_URL = 'http://127.0.0.1:11434/v1'
$env:LOCAL_LLM_MODEL = 'phi4-mini:latest'
uv run aig-plans
~~~

The example requires that exact model to be installed and Ollama to be running. If
stopped, start `ollama serve` in another terminal. Model downloads are an operator choice;
the application does not install or start models.

Open `http://127.0.0.1:8000/`. There is no separate frontend build or development server.
Enter a service-demo request, inspect the validated Plan, create a Run, then approve or
reject a write Plan using the current Run version. Read-only Runs become `Ready`;
approval does not dispatch an effect.

## Configuration and local execution demo

`LOCAL_LLM_BASE_URL` and `LOCAL_LLM_MODEL` are required. The URL must be HTTP on loopback
with an explicit port; `/v1` is normalized. Optional settings:

| Variable | Default |
| --- | --- |
| `MODEL_TIMEOUT_SECONDS` | `28`; maximum `28` |
| `API_HOST` / `API_PORT` | `127.0.0.1` / `8000` |
| `AIG_DB_PATH` | `.runtime/day2-runs.sqlite3` for the default v1 entry point |

Environment variables must actually be exported; `.env.example` is a reference, not an
automatically loaded configuration file. The provider uses bounded, non-streaming JSON
chat completions without retries, repair, or cloud fallback. Ollama is supported through
that protocol, not a dedicated SDK. The runtime does not manage model residency or hardware.

For executable Actions, use the explicit v2 setup in [Development](docs/DEVELOPMENT.md).
It requires a new/compatible v2 gate database, a separate provisioned demo receipt store,
a private capability file, and stable execution identity. Stop the predecessor and its
helpers before asserting `--predecessor-stopped`; never point this entry point at a v1
database expecting automatic migration.

In that configured UI: create a single-note Plan → approve → admit Action → authorize →
execute → refresh the durable result. Supply the local capability only in the UI's
memory-only input. Refresh/deep links read state; they do not replay mutations.

## Focused checks

~~~powershell
uv run pytest -q tests/test_sqlite_run_repository.py
uv run pytest -q tests/test_consumer_runtime.py
uv run pytest -q tests/test_dummy_adapter_contract.py tests/test_dummy_adapter_e2e.py
uv run ruff check src tests
uv run ruff format --check src tests
~~~

Test selection, browser prerequisites, and the complete offline command are in
[Development](docs/DEVELOPMENT.md). Live LLM tests are opt-in and excluded by default.

## Independent consumer

Use `ToolCapability`, `ToolBinding`, `ExecutableRegistry`, `BoundCapabilityAuthorization`,
and `open_runtime` from `agentic_intent_gate.runtime`. Registration is trusted Python
startup code, never a model or HTTP command. The consumer defines its own executor and
effect store; the package provides existing Run/approval/Action persistence and gates.

[examples/consumer/app.py](examples/consumer/app.py) demonstrates deterministic
`append_audit_entry/1` against `consumer-demo`, without Ollama or paid APIs. Its scripted
approval is test/example consent, not authenticated human approval. Build a local wheel
and run it outside the checkout using [Development](docs/DEVELOPMENT.md). The shared
runtime is released as the annotated `v0.1.0` Git tag; reproducible consumers should pin
its verified peeled commit as shown there. This Git release does not imply PyPI publication.

## Documentation

- [AGENTS.md](AGENTS.md): instructions for scoped agent-assisted development.
- [Architecture](docs/ARCHITECTURE.md): implemented boundaries, states, and APIs.
- [Adapter guide](docs/ADAPTER_GUIDE.md): safe competition-specific integration workflow.
- [Development](docs/DEVELOPMENT.md): environment, startup, tests, and troubleshooting.
- [Security](docs/SECURITY.md): guarantees, failure behavior, and limitations.

Historical development documents are recoverable from the permanent annotated
`day4-final-freeze` tag; they are not the current documentation system.
