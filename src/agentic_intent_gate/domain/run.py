"""Immutable persistent Run snapshots and the exhaustive frozen state graph."""

from dataclasses import InitVar, dataclass
from enum import StrEnum

from agentic_intent_gate.domain.approval import requires_approval
from agentic_intent_gate.domain.contracts import ERROR_MESSAGES, ErrorCode, Intent, Plan, Risk
from agentic_intent_gate.domain.run_validation import (
    check_contract,
    parse_timestamp,
    valid_uuid,
)
from agentic_intent_gate.domain.validation import INTENT_BYTES, PlanValidator, fail


class RunState(StrEnum):
    IntentReceived = "IntentReceived"
    PlanGenerating = "PlanGenerating"
    PlanValidated = "PlanValidated"
    Ready = "Ready"
    AwaitingApproval = "AwaitingApproval"
    Approved = "Approved"
    Rejected = "Rejected"
    PlanRejected = "PlanRejected"


TRANSITIONS = frozenset(
    {
        (RunState.IntentReceived, RunState.PlanGenerating),
        (RunState.IntentReceived, RunState.PlanRejected),
        (RunState.PlanGenerating, RunState.PlanValidated),
        (RunState.PlanGenerating, RunState.PlanRejected),
        (RunState.PlanValidated, RunState.Ready),
        (RunState.PlanValidated, RunState.AwaitingApproval),
        (RunState.AwaitingApproval, RunState.Approved),
        (RunState.AwaitingApproval, RunState.Rejected),
    }
)
TERMINAL_STATES = frozenset(
    {RunState.Ready, RunState.Approved, RunState.Rejected, RunState.PlanRejected}
)


class RunErrorCode(StrEnum):
    INVALID_APPROVAL = "INVALID_APPROVAL"
    RUN_NOT_FOUND = "RUN_NOT_FOUND"
    STALE_RUN_VERSION = "STALE_RUN_VERSION"
    INVALID_RUN_TRANSITION = "INVALID_RUN_TRANSITION"
    DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"
    DATABASE_SCHEMA_UNSUPPORTED = "DATABASE_SCHEMA_UNSUPPORTED"
    RECOVERY_INTERRUPTED = "RECOVERY_INTERRUPTED"


RUN_MESSAGES = {
    **ERROR_MESSAGES,
    RunErrorCode.INVALID_APPROVAL: "The request does not match the approval contract.",
    RunErrorCode.RUN_NOT_FOUND: "The Run was not found.",
    RunErrorCode.STALE_RUN_VERSION: "The Run version does not match expected_version.",
    RunErrorCode.INVALID_RUN_TRANSITION: "The Run state does not allow this operation.",
    RunErrorCode.DATABASE_UNAVAILABLE: "The Run store is unavailable.",
    RunErrorCode.DATABASE_SCHEMA_UNSUPPORTED: "The Run database schema is not supported.",
    RunErrorCode.RECOVERY_INTERRUPTED: (
        "Planning was interrupted before a validated Plan was stored."
    ),
}
PLANNING_ERRORS = frozenset(
    {
        ErrorCode.PLAN_VALIDATION_ERROR,
        ErrorCode.MODEL_UNAVAILABLE,
        ErrorCode.MODEL_TIMEOUT,
        ErrorCode.MODEL_RESPONSE_ERROR,
        ErrorCode.INTERNAL_ERROR,
        RunErrorCode.RECOVERY_INTERRUPTED,
    }
)


class RunStoreError(Exception):
    def __init__(self, code: ErrorCode | RunErrorCode, run_id: str | None = None):
        self.code = code
        self.run_id = run_id
        super().__init__(RUN_MESSAGES[code])


def validate_transition(before: RunState, after: RunState) -> None:
    if (
        not isinstance(before, RunState)
        or not isinstance(after, RunState)
        or (before, after) not in TRANSITIONS
    ):
        raise RunStoreError(RunErrorCode.INVALID_RUN_TRANSITION)


@dataclass(frozen=True)
class RunError:
    code: ErrorCode | RunErrorCode

    def __post_init__(self) -> None:
        if not isinstance(self.code, (ErrorCode, RunErrorCode)) or self.code not in PLANNING_ERRORS:
            fail("RUN_ERROR")

    def to_dict(self) -> dict:
        return {"code": self.code.value, "message": RUN_MESSAGES[self.code]}


@dataclass(frozen=True)
class Run:
    run_id: str
    intent: Intent
    state: RunState
    plan: Plan | None
    risk_class: Risk | None
    version: int
    created_at: str
    updated_at: str
    error: RunError | None = None
    plan_validator: InitVar[PlanValidator | None] = None

    def __post_init__(self, plan_validator) -> None:
        if not valid_uuid(self.run_id) or not isinstance(self.state, RunState):
            fail("RUN_ID_OR_STATE")
        if type(self.version) is not int or not isinstance(self.intent, Intent):
            fail("RUN_VERSION_OR_INTENT")
        try:
            if len(self.intent.text.encode("utf-8")) > INTENT_BYTES:
                fail("RUN_INTENT_BYTES")
        except (AttributeError, UnicodeError):
            fail("RUN_INTENT_BYTES")
        if self.plan is not None:
            if (
                not isinstance(self.plan, Plan)
                or not isinstance(self.risk_class, Risk)
                or self.plan.risk_class != self.risk_class
                or type(self.plan.steps) is not tuple
            ):
                fail("RUN_PLAN_OR_RISK")
        elif self.risk_class is not None:
            fail("RUN_PLAN_OR_RISK")
        if self.error is not None and not isinstance(self.error, RunError):
            fail("RUN_ERROR")
        if parse_timestamp(self.updated_at) < parse_timestamp(self.created_at):
            fail("RUN_TIME_ORDER")
        if plan_validator is None:
            check_contract("day2-run.schema.json", self.to_dict())
        else:
            plan_validator.validate_run_snapshot(self.to_dict())

    @property
    def approval_required(self) -> bool | None:
        return None if self.risk_class is None else requires_approval(self.risk_class)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "intent": {"text": self.intent.text},
            "state": self.state.value,
            "plan": self.plan.to_dict() if self.plan else None,
            "risk_class": self.risk_class.value if self.risk_class else None,
            "version": self.version,
            "approval_required": self.approval_required,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error.to_dict() if self.error else None,
        }
