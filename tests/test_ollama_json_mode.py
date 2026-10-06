"""JSON mode is a generation constraint, never a substitute for strict validation."""

import json
import tomllib
from pathlib import Path

import httpx
import pytest
import test_ollama_api_smoke as api_smoke
from live_helpers import ObservedTransport, validate_and_record

from agentic_intent_gate.adapters.configuration import Settings
from agentic_intent_gate.adapters.local_http import create_app
from agentic_intent_gate.adapters.models.local_openai import LocalOpenAIProvider
from agentic_intent_gate.application.generate_plan import PlanningService
from agentic_intent_gate.application.runs import RunService
from agentic_intent_gate.domain.contracts import ErrorCode, Intent, PlanningState, Risk
from agentic_intent_gate.domain.validation import ValidationFailure


def observed_provider(validator, envelope, raw):
    requests = []

    def handle(request):
        requests.append(request)
        payload = json.loads(request.content)
        assert request.method == "POST"
        assert str(request.url) == "http://127.0.0.1:11434/v1/chat/completions"
        assert payload["model"] == "phi-test"
        assert payload["response_format"] == {"type": "json_object"}
        assert payload["n"] == 1 and payload["stream"] is False
        return httpx.Response(200, json=envelope(raw))

    provider = LocalOpenAIProvider(
        "http://127.0.0.1:11434/v1", "phi-test", transport=httpx.MockTransport(handle)
    )
    return PlanningService(provider, validator), requests


async def test_json_mode_valid_plan_passes_strict_validation_once(validator, envelope, valid_plan):
    planning, requests = observed_provider(validator, envelope, json.dumps(valid_plan))
    result = await planning.generate_plan(Intent("Read service-demo"))
    assert result.state == PlanningState.PlanValidated
    assert result.error is None and result.plan.to_dict() == valid_plan
    assert result.plan.risk_class == Risk.READ_ONLY
    assert len(requests) == 1


@pytest.mark.parametrize(
    "kind,expected_code",
    [
        ("prose", "PARSE_INVALID_JSON"),
        ("fenced", "PARSE_INVALID_JSON"),
        ("trailing-prose", "PARSE_INVALID_JSON"),
        ("multiple-values", "PARSE_INVALID_JSON"),
        ("malformed", "PARSE_INVALID_JSON"),
        ("schema", "SCHEMA_REQUIRED"),
        ("risk-mismatch", "SEMANTIC_RISK_MISMATCH"),
    ],
)
async def test_json_mode_bad_candidate_fails_closed_without_retry(
    validator, envelope, valid_plan, kind, expected_code
):
    valid = json.dumps(valid_plan)
    if kind == "risk-mismatch":
        valid_plan["risk_class"] = "SIDE_EFFECT"
        raw = json.dumps(valid_plan)
    else:
        raw = {
            "prose": "Here is your plan.",
            "fenced": "```json\n" + valid + "\n```",
            "trailing-prose": valid + " Done.",
            "multiple-values": valid + " " + valid,
            "malformed": valid[:-1],
            "schema": "{}",
        }[kind]
    planning, requests = observed_provider(validator, envelope, raw)
    result = await planning.generate_plan(Intent("Read service-demo"))
    assert result.state == PlanningState.PlanRejected and result.plan is None
    assert result.error.code == ErrorCode.PLAN_VALIDATION_ERROR
    assert expected_code in {issue.code for issue in result.error.issues}
    assert len(requests) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("base_url", "http://127.0.0.1:1/v1"),
        ("model", "another-model"),
        ("LOCAL_LLM_BASE_URL", "http://127.0.0.1:1/v1"),
        ("LOCAL_LLM_MODEL", "another-model"),
        ("response_format", {"type": "text"}),
    ],
)
async def test_client_cannot_override_server_json_mode_or_runtime(
    validator, envelope, valid_plan, run_repository, field, value
):
    settings = Settings.from_environment(
        {"LOCAL_LLM_BASE_URL": "http://127.0.0.1:11434/v1", "LOCAL_LLM_MODEL": "phi-test"}
    )
    planning, requests = observed_provider(validator, envelope, json.dumps(valid_plan))
    assert planning.provider.endpoint == settings.endpoint
    assert planning.provider.model == settings.model
    app = create_app(planning, run_service=RunService(run_repository, planning))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        for path in ("/api/plans", "/api/runs"):
            response = await client.post(path, json={"text": "Read service-demo", field: value})
            assert response.status_code == 400
            assert response.json()["error"]["code"] == "INVALID_INTENT"
        assert not requests and not run_repository.recovery_candidates()
        response = await client.post("/api/plans", json={"text": "Read service-demo"})
        assert response.status_code == 200
    assert len(requests) == 1


def test_json_mode_does_not_require_an_sdk_or_agent_framework():
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text("utf-8"))
    assert {item.split(">=")[0] for item in project["project"]["dependencies"]} == {
        "fastapi",
        "httpx",
        "jsonschema",
        "uvicorn",
    }
    lock = tomllib.loads((root / "uv.lock").read_text("utf-8"))
    assert not any(
        package["name"].startswith(("ollama", "openai", "langchain", "llama-index"))
        for package in lock["package"]
    )


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("valid", ("PASS", "PASS", "PASS", "PASS")),
        ("parse", ("FAIL", "NOT_RUN", "NOT_RUN", "NOT_RUN")),
        ("schema", ("PASS", "FAIL", "NOT_RUN", "NOT_RUN")),
        ("risk", ("PASS", "PASS", "FAIL", "FAIL")),
    ],
)
def test_live_validation_evidence_only_marks_completed_stages(
    validator, valid_plan, kind, expected
):
    record = {}
    if kind == "risk":
        valid_plan["risk_class"] = "SIDE_EFFECT"
    raw = {"parse": "Not JSON", "schema": "{}"}.get(kind, json.dumps(valid_plan))
    if kind == "valid":
        assert validate_and_record(validator, raw, record).to_dict() == valid_plan
    else:
        with pytest.raises(ValidationFailure):
            validate_and_record(validator, raw, record)
    assert (
        tuple(
            record[key]
            for key in (
                "json_parse",
                "schema_validation",
                "semantic_validation",
                "trusted_risk_recomputation",
            )
        )
        == expected
    )
    assert raw not in json.dumps(record)


async def test_live_observation_forwards_one_request_without_recording_prompt(validator, envelope):
    observed = ObservedTransport()
    await observed.delegate.aclose()
    forwarded = []

    def handle(request):
        forwarded.append(request)
        return httpx.Response(200, json=envelope())

    observed.delegate = httpx.MockTransport(handle)
    provider = LocalOpenAIProvider("http://127.0.0.1:11434/v1", "phi-test", transport=observed)
    raw = await provider.generate_plan(
        Intent("Read service-demo"),
        tools=validator.tools,
        plan_schema=validator.plan_schema,
        timeout_seconds=1,
    )
    assert validator.parse_plan(raw).risk_class == Risk.READ_ONLY
    assert len(forwarded) == len(observed.requests) == 1
    assert observed.requests[0] == {
        "method": "POST",
        "url": "http://127.0.0.1:11434/v1/chat/completions",
        "model": "phi-test",
        "response_format": {"type": "json_object"},
    }


def test_api_smoke_harness_with_controlled_provider_not_live(
    validator, envelope, monkeypatch, tmp_path
):
    """Exercise the live harness and its SQL assertions without any real inference."""

    class ControlledTransport(ObservedTransport):
        def __init__(self):
            super().__init__()
            self.delegate = httpx.MockTransport(
                lambda request: httpx.Response(200, json=envelope())
            )

    monkeypatch.setattr(api_smoke, "ObservedTransport", ControlledTransport)
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("LOCAL_LLM_MODEL", "phi-test")
    monkeypatch.setenv("LIVE_MODEL_EVIDENCE_DIR", str(tmp_path))
    api_smoke.test_one_real_ollama_plans_then_one_runs(validator)
    record = json.loads((tmp_path / "api-smoke.json").read_text("utf-8"))
    assert record["api_plans_smoke"]["result"] == record["api_runs_smoke"]["result"] == "PASS"
    assert record["api_plans_smoke"]["model_calls"] == record["api_runs_smoke"]["model_calls"] == 1
    assert record["api_runs_smoke"]["persisted_state_flow"] == [
        "IntentReceived",
        "PlanGenerating",
        "PlanValidated",
        "Ready",
    ]
    assert record["server_stopped"] and record["temporary_database_removed"]
