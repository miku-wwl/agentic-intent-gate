"""Deterministic startup recovery; no provider, task, tool or network dependency."""

from agentic_intent_gate.domain.run import RunState
from agentic_intent_gate.ports.run_repository import RunRepository


def recover_runs(repository: RunRepository) -> None:
    for run in repository.recovery_candidates():
        if run.state == RunState.PlanValidated:
            repository.route_validated_plan(run.run_id, run.version, recovery=True)
        else:
            repository.recover_interrupted(run.run_id, run.version)
