"""Explicit v2 local execution composition; the frozen aig-plans/v1 entrypoint is unchanged."""

import argparse
import logging
import os
from pathlib import Path

import uvicorn

from agentic_intent_gate.api.local_http import create_app
from agentic_intent_gate.application.action_compiler import ActionCompiler
from agentic_intent_gate.application.action_execution import ActionExecutionGateway
from agentic_intent_gate.application.generate_plan import PlanningService
from agentic_intent_gate.application.recover_actions import recover_actions
from agentic_intent_gate.application.recover_runs import recover_runs
from agentic_intent_gate.application.runs import RunService
from agentic_intent_gate.config.configuration import ConfigurationError, Settings
from agentic_intent_gate.domain.action import ActionCode, ActionError
from agentic_intent_gate.domain.run import RunStoreError
from agentic_intent_gate.infrastructure.llm.local_openai import LocalOpenAIProvider
from agentic_intent_gate.infrastructure.persistence.sqlite_action_execution import (
    SQLiteExecutionRepository,
)
from agentic_intent_gate.infrastructure.persistence.sqlite_v2_runs import SQLiteV2RunRepository
from agentic_intent_gate.infrastructure.security.local_capability import (
    CapabilityVerifier,
    LocalDemoAuthorization,
)
from agentic_intent_gate.infrastructure.security.single_owner import single_owner
from agentic_intent_gate.infrastructure.tools.executable_registry import ExecutableRegistry
from agentic_intent_gate.infrastructure.tools.service_demo import DemoNoteExecutor
from agentic_intent_gate.infrastructure.tools.tool_catalog import build_validator


def compose(settings, environment, planning, *, predecessor_stopped=False):
    """Caller holds single_owner throughout serving; no migration or demo provisioning here."""
    if predecessor_stopped is not True:
        raise ActionError(ActionCode.NOT_AUTHORIZED)
    try:
        supplied = Path(environment["AIG_CAPABILITY_FILE"])
        path = supplied.resolve()
        if not path.is_file() or supplied.is_symlink() or path.stat().st_size != 64:
            raise ValueError
        if os.name != "nt" and path.stat().st_mode & 0o077:
            raise ValueError
        token = path.read_text("ascii")
        executor = DemoNoteExecutor(
            environment["AIG_DEMO_DB_PATH"],
            environment["AIG_EXECUTION_SCOPE"],
            gate_path=settings.db_path,
        )
        verifier = CapabilityVerifier(
            token,
            subject_id=environment["AIG_SUBJECT_ID"],
            binding=executor.binding,
            operations=("read", "admit", "authorize", "execute"),
        )
        policy = LocalDemoAuthorization(
            verifier, policy_revision=environment["AIG_POLICY_REVISION"]
        )
    except Exception:
        raise ConfigurationError("Explicit private execution configuration is required.") from None
    runs = SQLiteV2RunRepository(settings.db_path, planning.validator)
    registry = ExecutableRegistry(executor)
    actions = SQLiteExecutionRepository(runs, ActionCompiler(planning.validator, registry.bindings))
    recover_runs(runs)
    recover_actions(actions, predecessor_stopped=predecessor_stopped)
    return create_app(
        planning,
        run_service=RunService(runs, planning),
        action_gateway=ActionExecutionGateway(actions, policy, registry),
        capability_verifier=verifier,
    )


def main():
    parser = argparse.ArgumentParser(description="Single-owner explicit SQLite v2 demo execution")
    parser.add_argument("--predecessor-stopped", action="store_true", required=True)
    args = parser.parse_args()
    try:
        if "AIG_DB_PATH" not in os.environ:
            raise ConfigurationError("Explicit v2 gate path is required.")
        settings = Settings.from_environment()
        validator = build_validator()
        planning = PlanningService(
            LocalOpenAIProvider(settings.endpoint, settings.model),
            validator,
            model_timeout_seconds=settings.model_timeout_seconds,
        )
        with single_owner(settings.db_path, predecessor_stopped=args.predecessor_stopped):
            app = compose(settings, os.environ, planning, predecessor_stopped=True)
            uvicorn.run(
                app, host=settings.host, port=settings.port, log_level="info", access_log=False
            )
    except (ConfigurationError, ActionError, RunStoreError):
        logging.error(
            "Execution startup denied; inspect the private local configuration and ledger."
        )
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
