"""Small browser fixtures: installed Edge, actual HTTP servers, explicit live lane."""

import asyncio
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
import uvicorn
from action_helpers import IsolatedActionFixture, cmd
from playwright.sync_api import sync_playwright
from run_helpers import plan_for
from test_action_capability import capability
from test_action_persistence import ledger

from agentic_intent_gate.adapters.configuration import Settings
from agentic_intent_gate.adapters.executable_registry import ExecutableRegistry
from agentic_intent_gate.adapters.local_capability import CapabilityVerifier, LocalDemoAuthorization
from agentic_intent_gate.adapters.local_http import create_app
from agentic_intent_gate.adapters.service_demo import DemoNoteExecutor, provision_demo_store
from agentic_intent_gate.adapters.sqlite_action_execution import SQLiteExecutionRepository
from agentic_intent_gate.adapters.sqlite_run_repository import SQLiteRunRepository
from agentic_intent_gate.adapters.sqlite_v2_migration import migrate_v1_to_v2
from agentic_intent_gate.adapters.sqlite_v2_runs import SQLiteV2RunRepository
from agentic_intent_gate.application.action_compiler import ActionCompiler
from agentic_intent_gate.application.action_execution import ActionExecutionGateway
from agentic_intent_gate.application.generate_plan import PlanningService
from agentic_intent_gate.application.runs import RunService
from agentic_intent_gate.domain.approval import ApprovalChoice, ApprovalDecisionRequest
from agentic_intent_gate.domain.contracts import Intent, Risk
from agentic_intent_gate.domain.run import RunState
from agentic_intent_gate.ports.action_repository import AdmissionCommand
from agentic_intent_gate.ports.tool_executor import Outcome, ToolOutcome

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / ".runtime/browser-evidence" / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
EVIDENCE = {"recorded_at_utc": datetime.now(UTC).isoformat(), "tests": [], "servers": []}
ACTION_TEST_CREDENTIALS = []  # Memory-only; never serialized into evidence or diagnostics.


def port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False


@pytest.fixture(scope="module")
def browser():
    runtime = ROOT / ".runtime/browser-tests/tmp"
    runtime.mkdir(parents=True, exist_ok=True)
    channel = os.environ.get("AIG_BROWSER_CHANNEL", "msedge")
    with patch.dict(os.environ, {"TEMP": str(runtime), "TMP": str(runtime)}):
        with sync_playwright() as driver:
            browser = driver.chromium.launch(channel=channel, headless=True)
            record = {"channel": channel, "version": browser.version, "headless": True}
            EVIDENCE.setdefault("browsers", []).append(record)
            try:
                yield browser
            finally:
                browser.close()
                record["closed"] = not browser.is_connected()
        record["driver_context_exited"] = True


@pytest.fixture
def page(browser):
    context = browser.new_context(viewport={"width": 1000, "height": 900}, service_workers="block")
    page = context.new_page()
    page.set_default_timeout(5000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    yield page
    context.close()
    assert not errors, f"Unexpected browser JavaScript error: {errors}"


@contextmanager
def serve(app):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, log_level="warning", access_log=False, timeout_graceful_shutdown=2)
    )
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    record = {"lane": "DETERMINISTIC TEST", "port": port, "startup": "FAIL"}
    EVIDENCE["servers"].append(record)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline and thread.is_alive():
            time.sleep(0.01)
        assert server.started, "Deterministic HTTP server failed to start"
        record["startup"] = "PASS"
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(5)
        listener.close()
        record.update(thread_stopped=not thread.is_alive(), port_released=not port_open(port))
        assert record["thread_stopped"] and record["port_released"]


@pytest.fixture
def deterministic_app(validator, provider_factory):
    with ExitStack() as stack:

        def start(*, raw=None, error=None):
            provider = provider_factory(raw=raw, error=error)
            app = create_app(PlanningService(provider, validator))
            return stack.enter_context(serve(app)), provider

        yield start


@pytest.fixture
def ui_url(deterministic_app):
    url, _provider = deterministic_app()
    return url


@pytest.fixture
def run_app(validator, tmp_path):
    """Real HTTP/RunService/file DB; observe requests without changing responses."""
    stack = ExitStack()
    records = []

    def start(*, risk=Risk.READ_ONLY, raw=None, error=None, provider_gate=None, approval_gate=None):
        temporary = stack.enter_context(tempfile.TemporaryDirectory(prefix="run-ui-", dir=tmp_path))
        database = Path(temporary) / "runs.sqlite3"
        repository = SQLiteRunRepository(database, validator)

        class ControlledProvider:
            calls = 0

            async def generate_plan(self, intent, **kwargs):
                self.calls += 1
                if provider_gate is not None:
                    opened = await asyncio.to_thread(provider_gate.wait, 10)
                    assert opened, "Provider gate timed out"
                if error is not None:
                    raise error
                return raw if raw is not None else json.dumps(plan_for(validator, risk).to_dict())

        provider = ControlledProvider()
        planning = PlanningService(provider, validator)
        app = create_app(planning, run_service=RunService(repository, planning))
        requests = []

        @app.middleware("http")
        async def observe(request, call_next):
            entry = {"method": request.method, "path": request.url.path}
            requests.append(entry)
            if request.url.path.endswith("/approval") and approval_gate is not None:
                assert await asyncio.to_thread(approval_gate.wait, 10), "Approval gate timed out"
            response = await call_next(request)
            entry["status"] = response.status_code
            return response

        url = stack.enter_context(serve(app))
        record = {
            "url": url,
            "database": str(database),
            "real_http_run_service_sqlite": True,
            "browser_interception": False,
        }
        records.append((record, provider, database, provider_gate, approval_gate))
        EVIDENCE.setdefault("run_servers", []).append(record)
        return SimpleNamespace(
            url=url, repository=repository, database=database, provider=provider, requests=requests
        )

    try:
        yield start
    finally:
        for _record, _provider, _database, provider_gate, approval_gate in records:
            for gate in (provider_gate, approval_gate):
                if gate is not None:
                    gate.set()
        stack.close()  # Stop HTTP before deleting SQLite/WAL/SHM and its private directory.
        for record, provider, database, _provider_gate, _approval_gate in records:
            record.update(
                provider_calls=provider.calls, database_removed=not database.parent.exists()
            )
            assert record["database_removed"]


@pytest.fixture
def action_app(validator, tmp_path, monkeypatch):
    """Actual v2 HTTP composition. Secrets remain in memory; no traces/request headers saved."""
    with ExitStack() as stack:
        environments = []

        def prepare(awaitable):
            # Playwright's sync driver already owns a loop on the test thread.
            with ThreadPoolExecutor(max_workers=1) as worker:
                return worker.submit(asyncio.run, awaitable).result(timeout=10)

        def start(
            *,
            operations=("read", "admit", "authorize", "execute"),
            state=RunState.Approved,
            risk=Risk.SIDE_EFFECT,
            proposal=None,
            action_state=None,
            outcome="success",
            legacy=False,
            unconfigured=False,
        ):
            directory = Path(
                stack.enter_context(tempfile.TemporaryDirectory(dir=tmp_path, prefix="action-ui-"))
            )
            token, setup_verifier, setup_policy = capability()
            ACTION_TEST_CREDENTIALS.append(token)
            binding = setup_verifier.binding
            verifier = CapabilityVerifier(
                None if unconfigured else token,
                subject_id="local-demo",
                binding=binding,
                operations=operations,
            )
            policy = LocalDemoAuthorization(verifier, policy_revision="demo-policy-v1")
            path, demo = directory / "gate.sqlite3", directory / "downstream.sqlite3"
            runs = (SQLiteRunRepository if legacy else SQLiteV2RunRepository)(path, validator)

            class ControlledProvider:
                calls = 0

                async def generate_plan(self, intent, **kwargs):
                    self.calls += 1
                    return json.dumps(proposal or plan_for(validator, risk).to_dict())

            provider = ControlledProvider()
            planning = PlanningService(provider, validator)
            service = RunService(runs, planning)
            run = prepare(service.create(Intent("Controlled local demo proposal")))
            if state in {RunState.Approved, RunState.Rejected}:
                run = service.decide(
                    run.run_id,
                    ApprovalDecisionRequest(
                        ApprovalChoice.APPROVE
                        if state is RunState.Approved
                        else ApprovalChoice.REJECT,
                        4,
                    ),
                )
            if legacy:
                migrate_v1_to_v2(
                    path, directory / "private-v1-backup.sqlite3", validator, owners_stopped=True
                )
                runs = SQLiteV2RunRepository(path, validator)
                service = RunService(runs, planning)
            provision_demo_store(demo, binding.execution_scope)
            executor = DemoNoteExecutor(demo, binding.execution_scope, gate_path=path)
            repo = SQLiteExecutionRepository(runs, ActionCompiler(validator, (binding,)))
            e = IsolatedActionFixture(
                token=token,
                verifier=verifier,
                principal=setup_verifier.verify("Bearer " + token),
                setup_policy=setup_policy,
                runs=runs,
                run=run,
                repo=repo,
                database=path,
                demo_path=demo,
                executor=executor,
                calls=0,
                requests=[],
                gates=[],
                execute_gate=None,
                mutation_gate=None,
                barrier_operation=None,
                barrier_count=0,
                barrier=threading.Event(),
            )
            original = executor.execute

            async def controlled(spec, effect_key, context):
                e.calls += 1
                if e.execute_gate is not None:
                    if not await asyncio.to_thread(e.execute_gate.wait, 4):
                        raise TimeoutError("Controlled execution barrier timed out")
                result = await original(spec, effect_key, context)
                if outcome == "malicious":
                    return ToolOutcome(
                        Outcome.CONFIRMED_FAILURE,
                        effect_key,
                        spec.execution_scope,
                        spec.target,
                        spec.payload_digest(),
                        no_effect_reason='<img src=x onerror="window.actionAttack=1">',
                    )
                return ToolOutcome(Outcome.UNKNOWN_OUTCOME) if outcome == "unknown" else result

            monkeypatch.setattr(executor, "execute", controlled)
            registry = ExecutableRegistry(executor)
            gateway = ActionExecutionGateway(repo, policy, registry)
            setup_gateway = ActionExecutionGateway(repo, setup_policy, registry)
            e.gateway = gateway
            if action_state is not None:
                e.action = setup_gateway.admit(
                    AdmissionCommand(
                        run.run_id,
                        run.version,
                        e.principal.subject_id,
                        str(uuid4()),
                    ),
                    e.principal,
                ).action
                if action_state != "Pending":
                    e.action = setup_gateway.authorize(cmd(e.action), e.principal).action
                if action_state == "Executing":
                    e.action = repo.claim(cmd(e.action), e.principal, setup_policy).action
                elif action_state in {"Succeeded", "Failed", "Unknown"}:
                    if action_state == "Failed":
                        with closing(sqlite3.connect(demo)) as connection:
                            connection.execute(
                                "UPDATE resource SET execution_scope=?",
                                (str(uuid4()),),
                            )
                            connection.commit()
                    e.action = prepare(setup_gateway.execute(cmd(e.action), e.principal)).action
                assert e.action.state.value == action_state
            app = create_app(
                planning, run_service=service, action_gateway=gateway, capability_verifier=verifier
            )

            @app.middleware("http")
            async def observe(request, call_next):
                entry = {"method": request.method, "path": request.url.path}
                if request.method == "POST" and (
                    request.url.path.endswith("/actions") or "/api/actions/" in request.url.path
                ):
                    entry["idempotency_key"] = request.headers.get("idempotency-key")
                    if e.mutation_gate is not None:
                        assert await asyncio.to_thread(e.mutation_gate.wait, 4)
                    if request.url.path.endswith("/" + str(e.barrier_operation)):
                        e.barrier_count += 1
                        if e.barrier_count == 2:
                            e.barrier.set()
                        assert await asyncio.to_thread(e.barrier.wait, 4)
                e.requests.append(entry)
                response = await call_next(request)
                entry["status"] = response.status_code
                return response

            e.url = stack.enter_context(serve(app))
            e.ledger = lambda: ledger(path)

            def external_rows():
                with closing(sqlite3.connect(demo)) as connection:
                    return {
                        name: connection.execute(f"SELECT * FROM {name} ORDER BY 1").fetchall()
                        for name in ("notes", "receipts")
                    }

            e.external = external_rows
            e.provider = provider
            record = {
                "url": e.url,
                "run_id": run.run_id,
                "real_http_sqlite_v2_gateway": True,
                "capability_operations": sorted(operations),
                "outcome_fixture": outcome,
            }
            environments.append((e, record))
            EVIDENCE.setdefault("action_servers", []).append(record)
            return e

        try:
            yield start
        finally:
            for e, record in environments:
                for gate in (e.execute_gate, e.mutation_gate, e.barrier):
                    if gate is not None:
                        gate.set()
                external = e.external()
                record.update(
                    adapter_calls=e.calls,
                    note_count=len(external["notes"]),
                    receipt_count=len(external["receipts"]),
                    provider_calls=e.provider.calls,
                )
            # ExitStack stops HTTP before its owned temporary business directory is removed.
    for e, record in environments:
        record["database_removed"] = not e.database.parent.exists()
        assert record["database_removed"]


@pytest.fixture
def live_product():
    # Required settings are checked before launching. There is no mock/fallback branch.
    settings = Settings.from_environment()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.close()
    environment = os.environ.copy()
    environment.update(API_HOST="127.0.0.1", API_PORT=str(port))
    environment["PYTHONPATH"] = (
        os.pathsep.join((str(ROOT / "src"), str(Path(sys.prefix) / "Lib/site-packages")))
        if os.name == "nt"
        else str(ROOT / "src")
    )
    RUN.mkdir(parents=True, exist_ok=True)
    record = {
        "lane": "REAL PHI-4-MINI LIVE TEST",
        "endpoint": settings.endpoint,
        "wire_model": settings.model,
        "port": port,
        "startup": "FAIL",
    }
    EVIDENCE["servers"].append(record)
    with (RUN / "live-startup.log").open("w", encoding="utf-8") as log:
        # Base executable avoids the Windows venv redirector's extra child. Explicit
        # venv import paths select exactly the tested runtime dependencies.
        executable = sys._base_executable if os.name == "nt" else sys.executable
        process = subprocess.Popen(
            [executable, "-m", "agentic_intent_gate.main"],
            cwd=ROOT,
            env=environment,
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        record["pid"] = process.pid
        try:
            deadline = time.monotonic() + 15
            while not port_open(port) and time.monotonic() < deadline:
                assert process.poll() is None, "Real application exited before startup"
                time.sleep(0.05)
            assert port_open(port), "Real application did not start"
            record["startup"] = "PASS"
            yield f"http://127.0.0.1:{port}", record
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)
            record.update(
                process_stopped=True,
                exit_code=process.returncode,
                port_released=not port_open(port),
            )
            assert record["port_released"], "Real application port still listening"


@pytest.fixture
def browser_evidence():
    RUN.mkdir(parents=True, exist_ok=True)
    return EVIDENCE, RUN


def pytest_runtest_logreport(report):
    if "/browser/" in report.nodeid and report.when == "call":
        EVIDENCE["tests"].append(
            {
                "id": report.nodeid,
                "outcome": report.outcome,
                "duration_seconds": round(report.duration, 3),
                "lane": "REAL PHI-4-MINI LIVE TEST"
                if "test_live_browser" in report.nodeid
                else "DETERMINISTIC TEST",
            }
        )


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    if EVIDENCE["tests"]:
        RUN.mkdir(parents=True, exist_ok=True)
        EVIDENCE["pytest_exit_code"] = int(exitstatus)
        artifacts = [path for path in RUN.rglob("*") if path.is_file()]
        junit = session.config.option.xmlpath
        if junit and Path(junit).is_file():
            artifacts.append(Path(junit))
        exposed = any(
            token.encode("ascii") in path.read_bytes()
            for path in artifacts
            for token in ACTION_TEST_CREDENTIALS
        )
        EVIDENCE["action_artifact_credential_scan"] = "FAIL" if exposed else "PASS"
        EVIDENCE["action_managed_credential_count"] = len(ACTION_TEST_CREDENTIALS)
        (RUN / "results.json").write_text(
            json.dumps(EVIDENCE, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        if exposed:
            session.exitstatus = 1
            raise AssertionError("Managed Action credential exposed in retained artifacts")
