"""Composition and loopback-only process entrypoint."""

import logging

import uvicorn

from agentic_intent_gate.api.local_http import create_app
from agentic_intent_gate.application.generate_plan import PlanningService
from agentic_intent_gate.application.recover_runs import recover_runs
from agentic_intent_gate.application.runs import RunService
from agentic_intent_gate.config.configuration import ConfigurationError, Settings
from agentic_intent_gate.domain.run import RunStoreError
from agentic_intent_gate.infrastructure.llm.local_openai import LocalOpenAIProvider
from agentic_intent_gate.infrastructure.llm.provider_factory import create_model_provider
from agentic_intent_gate.infrastructure.persistence.sqlite_run_repository import SQLiteRunRepository
from agentic_intent_gate.infrastructure.tools.tool_catalog import build_validator

# Retain the module symbol used by existing restart tests and downstream test patches.
__all__ = ["LocalOpenAIProvider", "main"]


def main() -> None:
    try:
        settings = Settings.from_environment()
        provider = create_model_provider(settings)
    except ConfigurationError as exc:
        logging.error("Configuration error: %s", exc)
        raise SystemExit(2) from None
    validator = build_validator()
    try:
        repository = SQLiteRunRepository(settings.db_path, validator)
        recover_runs(repository)
    except RunStoreError as exc:
        logging.error("%s", exc)
        raise SystemExit(2) from None
    service = PlanningService(
        provider,
        validator,
        model_timeout_seconds=settings.model_timeout_seconds,
    )
    uvicorn.run(
        create_app(service, run_service=RunService(repository, service)),
        host=settings.host,
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
