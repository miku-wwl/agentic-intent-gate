"""Explicit application commands/queries, not publicly exposed HTTP or principal verification."""

from agentic_intent_gate.ports.action_repository import ActionRepository, AdmissionCommand


class ActionService:
    def __init__(self, repository: ActionRepository):
        self.repository = repository

    def admit(self, command: AdmissionCommand):
        return self.repository.admit(command)

    def get(self, action_id):
        return self.repository.get(action_id)

    def list_for_run(self, run_id):
        return self.repository.list_for_run(run_id)
