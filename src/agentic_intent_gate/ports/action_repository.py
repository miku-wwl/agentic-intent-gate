"""Internal application boundary only. A future verified transport must enforce visibility first."""

from dataclasses import dataclass
from typing import Protocol

from agentic_intent_gate.domain.action import Action, ActionCode, ActionError, identifier
from agentic_intent_gate.domain.run_validation import MAX_VERSION, valid_uuid
from agentic_intent_gate.domain.validation import REQUEST_BYTES, strict_object


@dataclass(frozen=True)
class AdmissionCommand:
    run_id: str
    expected_run_version: int
    subject_id: str
    idempotency_key: str

    def __post_init__(self):
        if (
            not valid_uuid(self.run_id)
            or not valid_uuid(self.idempotency_key)
            or not identifier(self.subject_id, limit=128)
            or type(self.expected_run_version) is not int
            or not 1 <= self.expected_run_version <= MAX_VERSION
        ):
            raise ActionError(ActionCode.INVALID_REQUEST)

    @classmethod
    def parse(cls, run_id, raw, *, subject_id, idempotency_key):
        value = strict_object(raw, max_bytes=REQUEST_BYTES)
        if set(value) != {"expected_run_version"}:
            raise ActionError(ActionCode.INVALID_REQUEST)
        return cls(run_id, value["expected_run_version"], subject_id, idempotency_key)


@dataclass(frozen=True)
class AdmissionResult:
    action: Action
    replayed: bool
    created: bool

    def to_dict(self):
        return {
            "run_id": self.action.spec.run_id,
            "actions": [self.action.snapshot()],
            "replayed": self.replayed,
        }


class ActionRepository(Protocol):
    def admit(self, command: AdmissionCommand) -> AdmissionResult: ...
    def get(self, action_id: str) -> Action | None: ...
    def list_for_run(self, run_id: str) -> tuple[Action, ...]: ...
    def validate_all(self) -> None: ...


@dataclass(frozen=True)
class ActionCommand:
    action_id: str
    expected_action_version: int
    idempotency_key: str

    def __post_init__(self):
        if (
            not valid_uuid(self.action_id)
            or not valid_uuid(self.idempotency_key)
            or type(self.expected_action_version) is not int
            or not 1 <= self.expected_action_version <= MAX_VERSION
        ):
            raise ActionError(ActionCode.INVALID_REQUEST)

    @classmethod
    def parse(cls, action_id, raw, *, idempotency_key):
        value = strict_object(raw, max_bytes=REQUEST_BYTES)
        if set(value) != {"expected_action_version"}:
            raise ActionError(ActionCode.INVALID_REQUEST)
        return cls(action_id, value["expected_action_version"], idempotency_key)


@dataclass(frozen=True)
class CommandResult:
    action: Action
    replayed: bool

    def to_dict(self):
        return {"action": self.action.snapshot(), "replayed": self.replayed}


@dataclass(frozen=True)
class ClaimResult(CommandResult):
    attempt_id: str | None = None


class ExecutionRepository(ActionRepository, Protocol):
    def authorize(self, command, principal, policy) -> CommandResult: ...
    def claim(self, command, principal, policy) -> ClaimResult: ...
    def finish(self, action_id, attempt_id, result) -> Action: ...
    def recover(self, *, owners_stopped: bool) -> int: ...
