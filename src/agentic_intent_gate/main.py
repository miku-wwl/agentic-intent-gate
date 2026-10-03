"""Composition and loopback-only process entrypoint."""

import logging

import uvicorn

from agentic_intent_gate.adapters.configuration import ConfigurationError, Settings
from agentic_intent_gate.adapters.local_http import create_app
from agentic_intent_gate.adapters.models.local_openai import LocalOpenAIProvider
from agentic_intent_gate.adapters.tool_catalog import build_validator
from agentic_intent_gate.application.generate_plan import PlanningService


def main() -> None:
    try:
        settings = Settings.from_environment()
    except ConfigurationError as exc:
        logging.error("Configuration error: %s", exc)
        raise SystemExit(2) from None
    service = PlanningService(
        LocalOpenAIProvider(settings.endpoint, settings.model),
        build_validator(),
        model_timeout_seconds=settings.model_timeout_seconds,
    )
    uvicorn.run(create_app(service), host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
