from pathlib import Path

import pytest

from agentic_intent_gate.adapters.configuration import ConfigurationError, Settings
from agentic_intent_gate.adapters.tool_catalog import build_validator


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://127.0.0.1:1234",
        "http://example.com:1234",
        "http://0.0.0.0:1234",
        "http://127.0.0.1:1234/v1/chat/completions",
        "http://127.0.0.1:1234?redirect=x",
        "http://user:password@127.0.0.1:1234",
        "http://127.0.0.1",
        "http://127.0.0.1:99999",
        "http://127.0.0.1:0",
        "http://127.0.0.1:1234?",
        "http://127.0.0.1:1234#",
        "http://127.0.0.1:\n1234",
    ],
)
def test_configuration_rejects_nonlocal_or_ambiguous_endpoint(endpoint):
    with pytest.raises(ConfigurationError):
        Settings(endpoint, "phi-4-mini")


@pytest.mark.parametrize("timeout", [0, -1, 29, float("nan"), float("inf")])
def test_timeout_is_bounded(timeout):
    with pytest.raises(ConfigurationError):
        Settings("http://127.0.0.1:1234", "phi-4-mini", model_timeout_seconds=timeout)


def test_required_environment_and_loopback_host():
    with pytest.raises(ConfigurationError):
        Settings.from_environment({})
    with pytest.raises(ConfigurationError):
        Settings("http://127.0.0.1:1234", "phi-4-mini", host="0.0.0.0")
    settings = Settings.from_environment(
        {
            "LOCAL_LLM_BASE_URL": "http://127.0.0.1:1234/v1/",
            "LOCAL_LLM_MODEL": "phi4-mini:latest",
            "MODEL_TIMEOUT_SECONDS": "2",
        }
    )
    assert settings.endpoint == "http://127.0.0.1:1234/v1"
    assert settings.model_timeout_seconds == 2


def test_canonical_schemas_load_without_repository_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    validator = build_validator()
    assert validator.plan_schema["required"] == ("goal", "target", "steps", "risk_class")
    assert Path.cwd() == tmp_path
