from pathlib import Path

import pytest

from agentic_intent_gate.config.configuration import (
    ANTHROPIC_ENDPOINT,
    OPENAI_ENDPOINT,
    ConfigurationError,
    Settings,
)
from agentic_intent_gate.infrastructure.tools.tool_catalog import build_validator


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


def test_legacy_ollama_settings_remain_the_default_provider():
    settings = Settings.from_environment(
        {
            "LOCAL_LLM_BASE_URL": "http://127.0.0.1:1234/v1/",
            "LOCAL_LLM_MODEL": "phi4-mini:latest",
        }
    )
    assert settings.provider == "ollama"
    assert settings.endpoint == "http://127.0.0.1:1234/v1"
    assert settings.model == "phi4-mini:latest"
    assert settings.api_key is None


@pytest.mark.parametrize(
    "environment,provider,endpoint,key",
    [
        (
            {
                "AIG_LLM_PROVIDER": "openai",
                "AIG_LLM_MODEL": "example-openai-model",
                "OPENAI_API_KEY": "openai-test-secret",
            },
            "openai",
            OPENAI_ENDPOINT,
            "openai-test-secret",
        ),
        (
            {
                "AIG_LLM_PROVIDER": "anthropic",
                "AIG_LLM_MODEL": "example-claude-model",
                "ANTHROPIC_API_KEY": "anthropic-test-secret",
            },
            "anthropic",
            ANTHROPIC_ENDPOINT,
            "anthropic-test-secret",
        ),
    ],
)
def test_named_cloud_provider_selection(environment, provider, endpoint, key):
    settings = Settings.from_environment(environment)
    assert settings.provider == provider
    assert settings.endpoint == endpoint
    assert settings.api_key == key
    assert key not in repr(settings)


def test_openai_compatible_provider_requires_a_public_https_endpoint(monkeypatch):
    import socket

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ],
    )
    settings = Settings.from_environment(
        {
            "AIG_LLM_PROVIDER": "openai-compatible",
            "AIG_LLM_MODEL": "vendor/model-id",
            "AIG_LLM_BASE_URL": "https://trusted-provider.com/v1/",
            "AIG_LLM_API_KEY": "compatible-test-secret",
        }
    )
    assert settings.endpoint == "https://trusted-provider.com/v1"
    assert settings.model == "vendor/model-id"

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", 443))],
    )
    with pytest.raises(ConfigurationError):
        Settings.from_environment(
            {
                "AIG_LLM_PROVIDER": "openai-compatible",
                "AIG_LLM_MODEL": "vendor/model-id",
                "AIG_LLM_BASE_URL": "https://trusted-provider.com/v1",
                "AIG_LLM_API_KEY": "compatible-test-secret",
            }
        )


@pytest.mark.parametrize(
    "environment",
    [
        {"AIG_LLM_PROVIDER": "unsupported", "AIG_LLM_MODEL": "model"},
        {"AIG_LLM_PROVIDER": "openai", "AIG_LLM_MODEL": "model"},
        {"AIG_LLM_PROVIDER": "anthropic", "AIG_LLM_MODEL": "model"},
        {
            "AIG_LLM_PROVIDER": "openai-compatible",
            "AIG_LLM_MODEL": "model",
            "AIG_LLM_API_KEY": "secret",
        },
        {
            "AIG_LLM_PROVIDER": "openai-compatible",
            "AIG_LLM_MODEL": "model",
            "AIG_LLM_BASE_URL": "https://127.0.0.1/v1",
            "AIG_LLM_API_KEY": "secret",
        },
        {
            "AIG_LLM_PROVIDER": "openai-compatible",
            "AIG_LLM_MODEL": "model",
            "AIG_LLM_BASE_URL": "http://provider.example/v1",
            "AIG_LLM_API_KEY": "secret",
        },
        {
            "AIG_LLM_PROVIDER": "openai-compatible",
            "AIG_LLM_MODEL": "model",
            "AIG_LLM_BASE_URL": "https://user:pass@provider.example/v1",
            "AIG_LLM_API_KEY": "secret",
        },
    ],
)
def test_invalid_provider_or_incomplete_cloud_configuration_fails_closed(environment):
    with pytest.raises(ConfigurationError):
        Settings.from_environment(environment)


def test_ollama_conflicting_aliases_are_rejected():
    with pytest.raises(ConfigurationError, match="conflict"):
        Settings.from_environment(
            {
                "LOCAL_LLM_BASE_URL": "http://127.0.0.1:1234/v1",
                "AIG_LLM_BASE_URL": "http://127.0.0.1:1235/v1",
                "LOCAL_LLM_MODEL": "legacy-model",
                "AIG_LLM_MODEL": "new-model",
            }
        )


def test_dotenv_is_optional_and_process_values_override_file(monkeypatch, tmp_path):
    keys = [
        "AIG_LLM_PROVIDER",
        "AIG_LLM_MODEL",
        "AIG_LLM_BASE_URL",
        "LOCAL_LLM_BASE_URL",
        "LOCAL_LLM_MODEL",
    ]
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    (tmp_path / ".env").write_text(
        "AIG_LLM_PROVIDER=ollama\n"
        "AIG_LLM_MODEL=from-file\n"
        "AIG_LLM_BASE_URL=http://127.0.0.1:11434/v1\n",
        encoding="utf-8",
    )
    settings = Settings.from_environment({}, dotenv_path=tmp_path / ".env")
    assert settings.model == "from-file"

    settings = Settings.from_environment(
        {"AIG_LLM_MODEL": "from-process"}, dotenv_path=tmp_path / ".env"
    )
    assert settings.model == "from-process"

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AIG_LLM_MODEL", "from-process-environment")
    settings = Settings.from_environment()
    assert settings.model == "from-process-environment"

    monkeypatch.delenv("AIG_LLM_MODEL")
    (tmp_path / ".env").unlink()
    settings = Settings.from_environment(
        {
            "LOCAL_LLM_BASE_URL": "http://127.0.0.1:11434/v1",
            "LOCAL_LLM_MODEL": "without-dotenv",
        }
    )
    assert settings.model == "without-dotenv"
