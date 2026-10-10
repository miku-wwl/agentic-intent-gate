"""Opt-in sequential real HTTP smoke, after the one direct provider smoke passes.

Exactly one /api/plans call, then one /api/runs call; stop on first failure.
No browser, fallback, model retry, raw-output evidence or external tool I/O.
"""

import json
import os
import socket
import sqlite3
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import uvicorn
from live_helpers import ObservedTransport, validate_and_record

from agentic_intent_gate.api.local_http import create_app
from agentic_intent_gate.application.generate_plan import PlanningService
from agentic_intent_gate.application.runs import RunService
from agentic_intent_gate.config.configuration import Settings
from agentic_intent_gate.infrastructure.llm.local_openai import LocalOpenAIProvider
from agentic_intent_gate.infrastructure.persistence.sqlite_run_repository import SQLiteRunRepository

pytestmark = pytest.mark.live


def test_one_real_ollama_plans_then_one_runs(validator):
    settings = Settings.from_environment()
    evidence = {
        "recorded_at_utc": datetime.now(UTC).isoformat(),
        "lane": "LIVE_REAL_HTTP_OLLAMA_NO_MOCK",
        "model": settings.model,
        "base_url": settings.endpoint,
        "api_plans_smoke": {"result": "NOT_RUN", "model_calls": 0},
        "api_runs_smoke": {"result": "NOT_RUN", "model_calls": 0},
        "tool_invocations": 0,
        "automatic_retries": 0,
    }
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    thread = None
    server = None
    output = Path(os.environ.get("LIVE_MODEL_EVIDENCE_DIR", ".runtime/ollama-migration/api"))
    output.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.TemporaryDirectory(prefix="aig-ollama-smoke-")
    database = Path(temporary.name) / "runs.sqlite3"
    observed = ObservedTransport()
    try:
        repository = SQLiteRunRepository(database, validator)
        planning = PlanningService(
            LocalOpenAIProvider(settings.endpoint, settings.model, transport=observed),
            validator,
            model_timeout_seconds=settings.model_timeout_seconds,
        )
        app = create_app(planning, run_service=RunService(repository, planning))
        server = uvicorn.Server(
            uvicorn.Config(app, log_level="warning", timeout_graceful_shutdown=2)
        )
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline and thread.is_alive():
            time.sleep(0.01)
        assert server.started, "Local smoke HTTP server did not start"
        body = {"text": "Plan to read the service-demo record."}
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=35
        ) as client:
            for path, key, expected_status in (
                ("/api/plans", "api_plans_smoke", 200),
                ("/api/runs", "api_runs_smoke", 201),
            ):
                count_before = len(observed.requests)
                started = time.monotonic()
                record = evidence[key]
                record["result"] = "FAIL"
                response = client.post(path, json=body)
                record.update(
                    http_status=response.status_code,
                    elapsed_seconds=round(time.monotonic() - started, 3),
                )
                payload = response.json()
                record["model_calls"] = len(observed.requests) - count_before
                if payload.get("error"):
                    record["safe_error_code"] = payload["error"]["code"]
                assert response.status_code == expected_status, (
                    "Live API did not return an accepted Plan"
                )
                plan = validate_and_record(validator, json.dumps(payload["plan"]), record)
                record.update(
                    state=payload["state"], risk_class=plan.risk_class.value, steps=len(plan.steps)
                )
                assert record["model_calls"] == 1
                assert observed.requests[-1]["response_format"] == {"type": "json_object"}
                if path == "/api/runs":
                    record.update(
                        run_id=payload["run_id"],
                        version=payload["version"],
                        approval_required=payload["approval_required"],
                    )
                    assert payload["state"] in {"Ready", "AwaitingApproval"}
                    assert repository.get(payload["run_id"]).to_dict() == payload
                    connection = sqlite3.connect(database)
                    try:
                        events = connection.execute(
                            "SELECT safe_payload_json FROM audit_events "
                            "WHERE run_id = ? ORDER BY run_version",
                            (payload["run_id"],),
                        ).fetchall()
                        assert (
                            connection.execute(
                                "SELECT count(*) FROM approval_decisions"
                            ).fetchone()[0]
                            == 0
                        )
                    finally:
                        connection.close()
                    record["persisted_state_flow"] = [
                        json.loads(row[0])["to_state"] for row in events
                    ]
                    assert record["persisted_state_flow"] == [
                        "IntentReceived",
                        "PlanGenerating",
                        "PlanValidated",
                        payload["state"],
                    ]
                    assert payload["version"] == 4
                else:
                    assert payload["state"] == "PlanValidated"
                record["result"] = "PASS"
        assert all(
            request["url"] == settings.endpoint + "/chat/completions"
            for request in observed.requests
        )
    finally:
        evidence["upstream_requests"] = observed.requests
        if server is not None:
            server.should_exit = True
        if thread is not None:
            thread.join(5)
        listener.close()
        evidence["server_stopped"] = thread is None or not thread.is_alive()
        temporary.cleanup()
        evidence["temporary_database_removed"] = not database.parent.exists()
        (output / "api-smoke.json").write_text(
            json.dumps(evidence, indent=2) + "\n", encoding="utf-8"
        )
        assert evidence["server_stopped"] and evidence["temporary_database_removed"]
