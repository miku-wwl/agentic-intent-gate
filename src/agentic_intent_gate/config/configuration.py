"""Environment settings for bounded local and cloud model providers."""

from __future__ import annotations

import ipaddress
import math
import os
import re
import socket
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values


class ConfigurationError(ValueError):
    pass


OPENAI_ENDPOINT = "https://api.openai.com/v1"
ANTHROPIC_ENDPOINT = "https://api.anthropic.com/v1"
PROVIDERS = {"ollama", "openai", "anthropic", "openai-compatible"}


def resolve_database_path(value: str | Path) -> Path:
    text = str(value)
    if (
        not text.strip()
        or text in {":memory:", ".", ".."}
        or text.lower().startswith("file:")
        or text.startswith(("\\\\", "//"))
        or any(ord(c) < 32 or ord(c) == 127 or c in "?#" for c in text)
        or ":" in text[2:]
    ):
        raise ConfigurationError("AIG_DB_PATH must select an ordinary local database file.")
    try:
        path = Path(text).resolve()
        if path.is_dir() or str(path).startswith(("\\\\", "//")):
            raise ValueError
        if os.name == "nt":
            import ctypes

            # Reject mapped remote drives as well as explicit UNC shares.
            if ctypes.windll.kernel32.GetDriveTypeW(path.anchor) == 4:
                raise ValueError
        return path
    except (OSError, ValueError):
        raise ConfigurationError(
            "AIG_DB_PATH must select an ordinary local database file."
        ) from None


def loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_endpoint(endpoint: str) -> str:
    """Retain the strict HTTP loopback contract for the existing Ollama provider."""
    try:
        parsed = urlsplit(endpoint)
        if (
            any(ord(character) <= 32 or ord(character) == 127 for character in endpoint)
            or "?" in endpoint
            or "#" in endpoint
            or parsed.scheme != "http"
            or not parsed.hostname
            or not loopback_host(parsed.hostname)
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/", "/v1", "/v1/")
            or parsed.query
            or parsed.fragment
            or parsed.port is None
            or not 1 <= parsed.port <= 65535
        ):
            raise ValueError
        base_url = endpoint.rstrip("/")
        return base_url if parsed.path.startswith("/v1") else base_url + "/v1"
    except ValueError:
        raise ConfigurationError(
            "LOCAL_LLM_BASE_URL must be a loopback HTTP API base URL with a port."
        ) from None


def validate_openai_compatible_endpoint(endpoint: str) -> str:
    """Validate a trusted operator endpoint before any credential is sent to it."""
    try:
        parsed = urlsplit(endpoint)
        host = parsed.hostname
        if (
            any(ord(character) <= 32 or ord(character) == 127 for character in endpoint)
            or "?" in endpoint
            or "#" in endpoint
            or parsed.scheme != "https"
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/", "/v1", "/v1/")
            or parsed.query
            or parsed.fragment
            or parsed.port not in (None, 443)
        ):
            raise ValueError

        normalized_host = host.encode("idna").decode("ascii").lower().rstrip(".")
        if normalized_host == "localhost" or normalized_host.endswith(
            (".localhost", ".local", ".internal", ".home", ".test", ".example", ".invalid")
        ):
            raise ValueError

        try:
            address = ipaddress.ip_address(normalized_host)
        except ValueError:
            if "." not in normalized_host or not re.fullmatch(r"[a-z0-9.-]+", normalized_host):
                raise ValueError from None
            addresses = {
                ipaddress.ip_address(result[4][0].split("%", 1)[0])
                for result in socket.getaddrinfo(normalized_host, 443, type=socket.SOCK_STREAM)
            }
            if not addresses or any(not item.is_global for item in addresses):
                raise ValueError from None
        else:
            if not address.is_global:
                raise ValueError

        path = "/v1"
        authority = f"[{normalized_host}]" if ":" in normalized_host else normalized_host
        return f"https://{authority}{path}"
    except (OSError, UnicodeError, ValueError):
        raise ConfigurationError(
            "AIG_LLM_BASE_URL must be a public HTTPS API base URL on port 443."
        ) from None


def _required_model(value: str | None, *, provider: str) -> str:
    if value is None:
        raise ConfigurationError("AIG_LLM_MODEL is required for the selected provider.")
    pattern = r"[A-Za-z0-9._:-]{1,200}" if provider == "ollama" else r"[A-Za-z0-9._:/-]{1,200}"
    if not re.fullmatch(pattern, value):
        raise ConfigurationError("AIG_LLM_MODEL must be a valid bounded model identifier.")
    return value


def _validate_secret_value(value: str | None, key: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 8192
        or value != value.strip()
        or any(not 33 <= ord(character) <= 126 for character in value)
    ):
        raise ConfigurationError(f"{key} must be set to a non-empty API key.")
    return value


def _required_secret(values: Mapping[str, str], key: str) -> str:
    return _validate_secret_value(values.get(key), key)


@dataclass(frozen=True)
class Settings:
    # The first two positional fields and their Ollama defaults preserve the v0.2.0
    # configuration constructor used by the existing local runtime and test helpers.
    endpoint: str
    model: str
    model_timeout_seconds: float = 28
    host: str = "127.0.0.1"
    port: int = 8000
    db_path: Path | str = ".runtime/day2-runs.sqlite3"
    provider: str = "ollama"
    api_key: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "db_path", resolve_database_path(self.db_path))
        if self.provider not in PROVIDERS:
            raise ConfigurationError("AIG_LLM_PROVIDER must name a supported provider.")
        if self.provider == "ollama":
            object.__setattr__(self, "endpoint", validate_endpoint(self.endpoint))
            _required_model(self.model, provider="ollama")
            if self.api_key is not None:
                raise ConfigurationError("Ollama does not accept a cloud API key.")
        elif self.provider == "openai":
            if self.endpoint != OPENAI_ENDPOINT:
                raise ConfigurationError("The OpenAI endpoint is fixed to its official HTTPS API.")
            _required_model(self.model, provider=self.provider)
            _validate_secret_value(self.api_key, "OPENAI_API_KEY")
        elif self.provider == "anthropic":
            if self.endpoint != ANTHROPIC_ENDPOINT:
                raise ConfigurationError(
                    "The Anthropic endpoint is fixed to its official HTTPS API."
                )
            _required_model(self.model, provider=self.provider)
            _validate_secret_value(self.api_key, "ANTHROPIC_API_KEY")
        else:
            object.__setattr__(self, "endpoint", validate_openai_compatible_endpoint(self.endpoint))
            _required_model(self.model, provider=self.provider)
            _validate_secret_value(self.api_key, "AIG_LLM_API_KEY")

        if (
            not math.isfinite(self.model_timeout_seconds)
            or not 0 < self.model_timeout_seconds <= 28
        ):
            raise ConfigurationError("MODEL_TIMEOUT_SECONDS must be greater than 0 and at most 28.")
        if not loopback_host(self.host) or not 1 <= self.port <= 65535:
            raise ConfigurationError(
                "API_HOST/API_PORT must select a loopback address and valid port."
            )

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
        *,
        dotenv_path: str | Path | None = None,
    ) -> Settings:
        """Load optional .env values, then apply process/explicit environment precedence."""
        try:
            values: dict[str, str] = {}
            if environment is None or dotenv_path is not None:
                env_path = Path(dotenv_path) if dotenv_path is not None else Path.cwd() / ".env"
                file_values = dotenv_values(env_path, interpolate=False)
                values.update(
                    {key: value for key, value in file_values.items() if value is not None}
                )
            values.update(os.environ if environment is None else environment)

            provider = values.get("AIG_LLM_PROVIDER", "ollama")
            if provider not in PROVIDERS:
                raise ConfigurationError("AIG_LLM_PROVIDER must name a supported provider.")

            if provider == "ollama":
                new_model = values.get("AIG_LLM_MODEL")
                legacy_model = values.get("LOCAL_LLM_MODEL")
                if new_model is None and legacy_model is None:
                    raise ConfigurationError(
                        "Set LOCAL_LLM_BASE_URL and LOCAL_LLM_MODEL or the AIG_LLM_* "
                        "Ollama settings."
                    )
                if new_model is not None and legacy_model is not None and new_model != legacy_model:
                    raise ConfigurationError(
                        "AIG_LLM_MODEL and LOCAL_LLM_MODEL conflict for Ollama."
                    )
                model = _required_model(
                    new_model if new_model is not None else legacy_model, provider=provider
                )
                new_endpoint = values.get("AIG_LLM_BASE_URL")
                legacy_endpoint = values.get("LOCAL_LLM_BASE_URL")
                if (
                    new_endpoint is not None
                    and legacy_endpoint is not None
                    and new_endpoint != legacy_endpoint
                ):
                    raise ConfigurationError(
                        "AIG_LLM_BASE_URL and LOCAL_LLM_BASE_URL conflict for Ollama."
                    )
                endpoint = new_endpoint if new_endpoint is not None else legacy_endpoint
                if endpoint is None:
                    raise ConfigurationError(
                        "Set AIG_LLM_BASE_URL or legacy LOCAL_LLM_BASE_URL for Ollama."
                    )
                api_key = None
            elif provider == "openai":
                if values.get("AIG_LLM_BASE_URL"):
                    raise ConfigurationError(
                        "AIG_LLM_BASE_URL is only used by Ollama and openai-compatible."
                    )
                endpoint = OPENAI_ENDPOINT
                model = _required_model(values.get("AIG_LLM_MODEL"), provider=provider)
                api_key = _required_secret(values, "OPENAI_API_KEY")
            elif provider == "anthropic":
                if values.get("AIG_LLM_BASE_URL"):
                    raise ConfigurationError(
                        "AIG_LLM_BASE_URL is only used by Ollama and openai-compatible."
                    )
                endpoint = ANTHROPIC_ENDPOINT
                model = _required_model(values.get("AIG_LLM_MODEL"), provider=provider)
                api_key = _required_secret(values, "ANTHROPIC_API_KEY")
            else:
                # Explicit cloud selection makes the legacy Ollama-only names inactive.
                endpoint = values.get("AIG_LLM_BASE_URL", "")
                model = _required_model(values.get("AIG_LLM_MODEL"), provider=provider)
                api_key = _required_secret(values, "AIG_LLM_API_KEY")

            return cls(
                endpoint=endpoint,
                model=model,
                model_timeout_seconds=float(values.get("MODEL_TIMEOUT_SECONDS", "28")),
                host=values.get("API_HOST", "127.0.0.1"),
                port=int(values.get("API_PORT", "8000")),
                db_path=values.get("AIG_DB_PATH", ".runtime/day2-runs.sqlite3"),
                provider=provider,
                api_key=api_key,
            )
        except ConfigurationError:
            raise
        except (KeyError, OSError, TypeError, ValueError) as exc:
            raise ConfigurationError(
                "Set valid LLM provider, model, endpoint, API, and local server settings."
            ) from exc
