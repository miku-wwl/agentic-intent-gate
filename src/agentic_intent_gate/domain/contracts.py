"""Frozen Day 1 terminology. Approval/ExecutionResult remain deferred Stage 1 contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType


class Risk(StrEnum):
    READ_ONLY = "READ_ONLY"
    SIDE_EFFECT = "SIDE_EFFECT"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"


class PlanningState(StrEnum):
    IntentReceived = "IntentReceived"
    PlanGenerating = "PlanGenerating"
    PlanGenerated = "PlanGenerated"
    PlanValidated = "PlanValidated"
    PlanRejected = "PlanRejected"


class ErrorCode(StrEnum):
    INVALID_INTENT = "INVALID_INTENT"
    REQUEST_TOO_LARGE = "REQUEST_TOO_LARGE"
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    PLAN_VALIDATION_ERROR = "PLAN_VALIDATION_ERROR"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    MODEL_TIMEOUT = "MODEL_TIMEOUT"
    MODEL_RESPONSE_ERROR = "MODEL_RESPONSE_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


ERROR_MESSAGES = {
    ErrorCode.INVALID_INTENT: "The request does not match the Intent contract.",
    ErrorCode.REQUEST_TOO_LARGE: "The request exceeds the allowed size.",
    ErrorCode.UNSUPPORTED_MEDIA_TYPE: "Use application/json with UTF-8 encoding.",
    ErrorCode.PLAN_VALIDATION_ERROR: "The generated proposal failed validation.",
    ErrorCode.MODEL_UNAVAILABLE: "The configured local model is unavailable.",
    ErrorCode.MODEL_TIMEOUT: "The planning deadline was exceeded.",
    ErrorCode.MODEL_RESPONSE_ERROR: "The local model returned an invalid response envelope.",
    ErrorCode.INTERNAL_ERROR: "An internal planning error occurred.",
}
HTTP_STATUSES = {
    ErrorCode.INVALID_INTENT: 400,
    ErrorCode.REQUEST_TOO_LARGE: 413,
    ErrorCode.UNSUPPORTED_MEDIA_TYPE: 415,
    ErrorCode.PLAN_VALIDATION_ERROR: 422,
    ErrorCode.MODEL_UNAVAILABLE: 503,
    ErrorCode.MODEL_TIMEOUT: 504,
    ErrorCode.MODEL_RESPONSE_ERROR: 502,
    ErrorCode.INTERNAL_ERROR: 500,
}


def freeze_json(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({key: freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze_json(item) for item in value)
    return value


def thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: thaw_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw_json(item) for item in value]
    return value


@dataclass(frozen=True)
class Intent:
    text: str


@dataclass(frozen=True)
class PlanStep:
    tool_name: str
    tool_version: str
    arguments: Mapping[str, str | int | bool]

    def __post_init__(self) -> None:
        object.__setattr__(self, "arguments", MappingProxyType(dict(self.arguments)))

    def to_dict(self) -> dict:
        return {
            "tool_name": self.tool_name,
            "tool_version": self.tool_version,
            "arguments": dict(self.arguments),
        }


@dataclass(frozen=True)
class Plan:
    goal: str
    target: str
    steps: tuple[PlanStep, ...]
    risk_class: Risk

    def to_dict(self) -> dict:
        return {
            "goal": self.goal,
            "target": self.target,
            "steps": [step.to_dict() for step in self.steps],
            "risk_class": self.risk_class.value,
        }


@dataclass(frozen=True)
class Tool:
    name: str
    version: str
    description: str
    input_schema: Mapping[str, object]
    risk_class: Risk

    def __post_init__(self) -> None:
        object.__setattr__(self, "input_schema", freeze_json(self.input_schema))


@dataclass(frozen=True)
class ValidationIssue:
    path: str
    code: str


@dataclass(frozen=True)
class PlanningError:
    code: ErrorCode
    issues: tuple[ValidationIssue, ...] = ()

    def to_dict(self) -> dict:
        return {
            "code": self.code.value,
            "message": ERROR_MESSAGES[self.code],
            "issues": [{"path": issue.path, "code": issue.code} for issue in self.issues[:10]],
        }


@dataclass(frozen=True)
class PlanningResult:
    state: PlanningState | None
    plan: Plan | None
    error: PlanningError | None
    # Internal request-local transition evidence, never added to the wire contract.
    states: tuple[PlanningState, ...] = ()

    @property
    def http_status(self) -> int:
        return HTTP_STATUSES[self.error.code] if self.error else 200

    def to_dict(self) -> dict:
        return {
            "state": self.state.value if self.state else None,
            "plan": self.plan.to_dict() if self.plan else None,
            "error": self.error.to_dict() if self.error else None,
        }

    @classmethod
    def reject(
        cls,
        code: ErrorCode,
        *,
        issues: tuple[ValidationIssue, ...] = (),
        states: tuple[PlanningState, ...] = (),
        accepted: bool = True,
    ) -> PlanningResult:
        state = PlanningState.PlanRejected if accepted else None
        return cls(state, None, PlanningError(code, issues[:10]), (*states, state) if state else ())
