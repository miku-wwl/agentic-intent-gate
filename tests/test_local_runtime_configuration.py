"""Runtime migration guards; no Ollama installation, daemon or inference is required."""

import json

import httpx
import pytest

from agentic_intent_gate.adapters.configuration import ConfigurationError, Settings
from agentic_intent_gate.adapters.local_http import create_app
from agentic_intent_gate.adapters.models.local_openai import LocalOpenAIProvider
from agentic_intent_gate.application.generate_plan import PlanningService
from agentic_intent_gate.application.runs import RunService
from agentic_intent_gate.domain.contracts import Intent


@pytest.mark.parametrize(
    "environment",
    [
        {},
        {"LOCAL_LLM_BASE_URL": "http://127.0.0.1:11434/v1"},
        {"LOCAL_LLM_MODEL": "phi4-mini:latest"},
        {"FOUNDRY_LOCAL_ENDPOINT": "http://127.0.0.1:1", "FOUNDRY_LOCAL_MODEL": "retired"},
    ],
)
def test_generic_variables_required_without_retired_alias(environment):
    with pytest.raises(ConfigurationError):
        Settings.from_environment(environment)


@pytest.mark.parametrize(
    "base_url",
    [
        "http://127.0.0.1:11434",
        "http://127.0.0.1:11434/",
        "http://127.0.0.1:11434/v1",
        "http://127.0.0.1:11434/v1/",
    ],
)
async def test_canonical_v1_base_and_exact_wire_path(validator, envelope, base_url):
    settings = Settings.from_environment(
        {"LOCAL_LLM_BASE_URL": base_url, "LOCAL_LLM_MODEL": "phi-test"}
    )
    assert settings.endpoint == "http://127.0.0.1:11434/v1"
    calls = []

    def handle(request):
        calls.append(request)
        assert str(request.url) == settings.endpoint + "/chat/completions"
        assert json.loads(request.content)["model"] == "phi-test"
        assert request.extensions["timeout"] == {"connect": 2.0, "read": 3, "write": 3, "pool": 3}
        return httpx.Response(200, json=envelope())

    provider = LocalOpenAIProvider(
        settings.endpoint, settings.model, transport=httpx.MockTransport(handle)
    )
    await provider.generate_plan(
        Intent("Read service-demo"),
        tools=validator.tools,
        plan_schema=validator.plan_schema,
        timeout_seconds=3,
    )
    assert len(calls) == 1


@pytest.mark.parametrize(
    "value", ["", "http://example.com:11434/v1", "http://127.0.0.1:11434/v1/v1"]
)
def test_generic_base_url_rejects_invalid_remote_or_ambiguous_path(value):
    with pytest.raises(ConfigurationError):
        Settings.from_environment(
            {"LOCAL_LLM_BASE_URL": value, "LOCAL_LLM_MODEL": "phi4-mini:latest"}
        )


@pytest.mark.parametrize("model", ["", "phi4-mini latest", "phi4-mini/../../secret"])
def test_generic_model_tag_is_required_and_bounded(model):
    with pytest.raises(ConfigurationError):
        Settings.from_environment(
            {"LOCAL_LLM_BASE_URL": "http://127.0.0.1:11434/v1", "LOCAL_LLM_MODEL": model}
        )


@pytest.mark.parametrize("field", ["base_url", "model", "LOCAL_LLM_BASE_URL", "LOCAL_LLM_MODEL"])
async def test_request_cannot_override_runtime(validator, provider_factory, run_repository, field):
    provider = provider_factory()
    planning = PlanningService(provider, validator)
    app = create_app(planning, run_service=RunService(run_repository, planning))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        for path in ("/api/plans", "/api/runs"):
            response = await client.post(
                path, json={"text": "Read service-demo", field: "override"}
            )
            assert (
                response.status_code == 400 and response.json()["error"]["code"] == "INVALID_INTENT"
            )
    assert provider.calls == 0 and not run_repository.recovery_candidates()


def test_missing_generic_runtime_config_fails_before_serving(monkeypatch, caplog):
    from agentic_intent_gate.main import main

    monkeypatch.delenv("LOCAL_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LOCAL_LLM_MODEL", raising=False)
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert "LOCAL_LLM_BASE_URL" in caplog.text and "LOCAL_LLM_MODEL" in caplog.text
