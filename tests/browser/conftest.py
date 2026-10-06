"""Small browser fixtures: installed Edge, actual HTTP servers, explicit live lane."""

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import uvicorn
from playwright.sync_api import sync_playwright
from run_helpers import plan_for

from agentic_intent_gate.adapters.configuration import Settings
from agentic_intent_gate.adapters.local_http import create_app
from agentic_intent_gate.adapters.sqlite_run_repository import SQLiteRunRepository
from agentic_intent_gate.application.generate_plan import PlanningService
from agentic_intent_gate.application.runs import RunService
from agentic_intent_gate.domain.contracts import Risk

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / ".runtime/browser-evidence" / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
EVIDENCE = {"recorded_at_utc": datetime.now(UTC).isoformat(), "tests": [], "servers": []}


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
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", timeout_graceful_shutdown=2))
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


def pytest_sessionfinish(session, exitstatus):
    if EVIDENCE["tests"]:
        RUN.mkdir(parents=True, exist_ok=True)
        EVIDENCE["pytest_exit_code"] = int(exitstatus)
        (RUN / "results.json").write_text(
            json.dumps(EVIDENCE, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
