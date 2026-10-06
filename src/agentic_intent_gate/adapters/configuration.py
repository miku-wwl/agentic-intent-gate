"""Required local model configuration and bounded loopback server settings."""

from __future__ import annotations

import ipaddress
import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


class ConfigurationError(ValueError):
    pass


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
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or parsed.port is None
            or not 1 <= parsed.port <= 65535
        ):
            raise ValueError
        return endpoint.rstrip("/")
    except ValueError as exc:
        raise ConfigurationError(
            "FOUNDRY_LOCAL_ENDPOINT must be a loopback HTTP base URL with a port."
        ) from exc


@dataclass(frozen=True)
class Settings:
    endpoint: str
    model: str
    model_timeout_seconds: float = 28
    host: str = "127.0.0.1"
    port: int = 8000
    db_path: Path | str = ".runtime/day2-runs.sqlite3"

    def __post_init__(self) -> None:
        object.__setattr__(self, "db_path", resolve_database_path(self.db_path))
        object.__setattr__(self, "endpoint", validate_endpoint(self.endpoint))
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,200}", self.model):
            raise ConfigurationError("FOUNDRY_LOCAL_MODEL must be the configured model wire ID.")
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
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> Settings:
        values = os.environ if environment is None else environment
        try:
            return cls(
                endpoint=values["FOUNDRY_LOCAL_ENDPOINT"],
                model=values["FOUNDRY_LOCAL_MODEL"],
                model_timeout_seconds=float(values.get("MODEL_TIMEOUT_SECONDS", "28")),
                host=values.get("API_HOST", "127.0.0.1"),
                port=int(values.get("API_PORT", "8000")),
                db_path=values.get("AIG_DB_PATH", ".runtime/day2-runs.sqlite3"),
            )
        except (KeyError, ValueError) as exc:
            if isinstance(exc, ConfigurationError):
                raise
            raise ConfigurationError(
                "Set valid local model and API environment variables."
            ) from exc
