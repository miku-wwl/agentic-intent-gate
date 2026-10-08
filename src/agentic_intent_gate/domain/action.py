"""Independent Action value/graph contracts. No lifecycle mutation is implemented here."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from agentic_intent_gate.domain.approval_binding import is_digest
from agentic_intent_gate.domain.contracts import Risk, freeze_json, thaw_json
from agentic_intent_gate.domain.run_validation import canonical_json, parse_timestamp, valid_uuid
from agentic_intent_gate.domain.validation import fail, strict_object


class ActionState(StrEnum):
    Pending = "Pending"
    Authorized = "Authorized"
    Executing = "Executing"
    Succeeded = "Succeeded"
    Failed = "Failed"
    Unknown = "Unknown"


ACTION_EDGES = frozenset(
    {
        (ActionState.Pending, ActionState.Authorized),
        (ActionState.Authorized, ActionState.Executing),
        (ActionState.Executing, ActionState.Succeeded),
        (ActionState.Executing, ActionState.Failed),
        (ActionState.Executing, ActionState.Unknown),
    }
)
RECONCILIATION_EDGES = frozenset(
    {(ActionState.Unknown, ActionState.Succeeded), (ActionState.Unknown, ActionState.Failed)}
)


class ActionCode(StrEnum):
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
    NOT_AUTHORIZED = "ACTION_NOT_AUTHORIZED"
    KEY_REQUIRED = "IDEMPOTENCY_KEY_REQUIRED"
    ALREADY_STARTED = "EXECUTION_ALREADY_STARTED"
    TOOL_REJECTED = "TOOL_REJECTED"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    INVALID_REQUEST = "INVALID_ACTION_REQUEST"
    NOT_FOUND = "ACTION_NOT_FOUND"
    STALE_VERSION = "STALE_ACTION_VERSION"
    NOT_EXECUTABLE = "ACTION_NOT_EXECUTABLE"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    RUN_NOT_ELIGIBLE = "RUN_NOT_ACTION_ELIGIBLE"
    TOOL_NOT_BOUND = "TOOL_NOT_BOUND"
    PLAN_NOT_EXECUTABLE = "PLAN_NOT_EXECUTABLE"
    DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"


ACTION_MESSAGES = {
    ActionCode.AUTHENTICATION_REQUIRED: "Verified execution credentials are required.",
    ActionCode.NOT_AUTHORIZED: "The execution policy does not permit this operation.",
    ActionCode.KEY_REQUIRED: "A stable Idempotency-Key is required.",
    ActionCode.ALREADY_STARTED: "This Action already has an execution attempt.",
    ActionCode.TOOL_REJECTED: "The compiled tool input was rejected.",
    ActionCode.INTERNAL_ERROR: "An internal Action error occurred.",
    ActionCode.INVALID_REQUEST: "The request does not match the Action contract.",
    ActionCode.NOT_FOUND: "The Action was not found.",
    ActionCode.STALE_VERSION: "The Action version does not match expected_action_version.",
    ActionCode.NOT_EXECUTABLE: "The Action state does not allow this operation.",
    ActionCode.IDEMPOTENCY_CONFLICT: "The request key is bound to different content.",
    ActionCode.RUN_NOT_ELIGIBLE: "The Run does not allow Action admission.",
    ActionCode.TOOL_NOT_BOUND: "The selected tool has no permitted execution binding.",
    ActionCode.PLAN_NOT_EXECUTABLE: "The Plan is outside the supported execution scope.",
    ActionCode.DATABASE_UNAVAILABLE: "The Action store is unavailable.",
}


class ActionError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(ACTION_MESSAGES[code])


def validate_action_edge(before, after, *, reconciliation=False):
    edges = ACTION_EDGES | (RECONCILIATION_EDGES if reconciliation else frozenset())
    if (
        not isinstance(before, ActionState)
        or not isinstance(after, ActionState)
        or (before, after) not in edges
    ):
        raise ActionError(ActionCode.NOT_EXECUTABLE)


def identifier(value, *, limit=64):
    return (
        isinstance(value, str)
        and len(value) <= limit
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value) is not None
    )


@dataclass(frozen=True)
class CompiledSpec:
    run_id: str
    approved_run_version: int
    approval_decision_id: str
    plan_digest: str
    step_index: int
    tool_name: str
    tool_version: str
    target: str
    normalized_arguments: Mapping
    risk_class: Risk
    compiler_revision: str
    adapter_id: str
    binding_revision: str
    execution_scope: str
    spec_digest: str

    def __post_init__(self):
        if not all(
            valid_uuid(v) for v in (self.run_id, self.approval_decision_id, self.execution_scope)
        ):
            fail("ACTION_SPEC_ID")
        if type(self.approved_run_version) is not int or self.approved_run_version != 5:
            fail("ACTION_RUN_VERSION")
        if type(self.step_index) is not int or not 0 <= self.step_index <= 4:
            fail("ACTION_STEP_INDEX")
        if not all(
            identifier(v)
            for v in (
                self.tool_name,
                self.tool_version,
                self.target,
                self.compiler_revision,
                self.adapter_id,
                self.binding_revision,
            )
        ):
            fail("ACTION_SPEC_IDENTIFIER")
        if (
            not all(is_digest(v) for v in (self.plan_digest, self.spec_digest))
            or self.risk_class is not Risk.SIDE_EFFECT
        ):
            fail("ACTION_SPEC_DIGEST_OR_RISK")
        value = strict_object(canonical_json(thaw_json(self.normalized_arguments)), max_bytes=4096)
        object.__setattr__(self, "normalized_arguments", freeze_json(value))

    def to_dict(self):
        return vars(self) | {
            "normalized_arguments": thaw_json(self.normalized_arguments),
            "risk_class": self.risk_class.value,
        }


@dataclass(frozen=True)
class Action:
    action_id: str
    spec: CompiledSpec
    effect_key: str
    created_at: str
    state: ActionState = ActionState.Pending
    version: int = 1
    updated_at: str | None = None
    result: Mapping | None = None
    authorization: Mapping | None = None

    def __post_init__(self):
        if (
            not valid_uuid(self.action_id)
            or not valid_uuid(self.effect_key)
            or not isinstance(self.spec, CompiledSpec)
        ):
            fail("ACTION_ID_OR_SPEC")
        parse_timestamp(self.created_at)
        if self.updated_at is None:
            object.__setattr__(self, "updated_at", self.created_at)
        parse_timestamp(self.updated_at)
        versions = {
            ActionState.Pending: {1},
            ActionState.Authorized: {2},
            ActionState.Executing: {3},
            ActionState.Succeeded: {4, 5},
            ActionState.Failed: {4, 5},
            ActionState.Unknown: {4},
        }
        if (
            not isinstance(self.state, ActionState)
            or type(self.version) is not int
            or self.version not in versions[self.state]
        ):
            fail("ACTION_STATE_VERSION")
        if self.updated_at < self.created_at or (
            self.state is ActionState.Pending and self.updated_at != self.created_at
        ):
            fail("ACTION_TIME")
        if self.state in {ActionState.Pending, ActionState.Authorized, ActionState.Executing}:
            if self.result is not None:
                fail("ACTION_PRE_RESULT")
        else:
            expected = {
                ActionState.Succeeded: (
                    "EXECUTION_SUCCEEDED",
                    "The demo note effect was confirmed.",
                ),
                ActionState.Failed: (
                    "EXECUTION_CONFIRMED_FAILED",
                    "The external effect was confirmed not to have occurred.",
                ),
                ActionState.Unknown: (
                    "EXECUTION_OUTCOME_UNKNOWN",
                    "The external effect outcome is unknown.",
                ),
            }[self.state]
            value = strict_object(canonical_json(thaw_json(self.result)), max_bytes=1024)
            if (
                set(value) != {"code", "message", "receipt_reference"}
                or (value["code"], value["message"]) != expected
            ):
                fail("ACTION_RESULT")
            if (
                self.state is ActionState.Succeeded and not valid_uuid(value["receipt_reference"])
            ) or (
                self.state is not ActionState.Succeeded and value["receipt_reference"] is not None
            ):
                fail("ACTION_RECEIPT_REFERENCE")
            object.__setattr__(self, "result", freeze_json(value))
        if self.authorization is not None:
            value = strict_object(canonical_json(thaw_json(self.authorization)), max_bytes=1024)
            if (
                set(value) != {"authorization_id", "subject_id", "policy_revision", "authorized_at"}
                or not valid_uuid(value["authorization_id"])
                or not identifier(value["subject_id"], limit=128)
                or not identifier(value["policy_revision"])
            ):
                fail("ACTION_AUTHORIZATION")
            parse_timestamp(value["authorized_at"])
            object.__setattr__(self, "authorization", freeze_json(value))

    def snapshot(self):
        spec = self.spec.to_dict()
        return {
            name: spec[name]
            for name in (
                "run_id",
                "step_index",
                "tool_name",
                "tool_version",
                "target",
                "normalized_arguments",
                "risk_class",
            )
        } | {
            "action_id": self.action_id,
            "state": self.state.value,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "authorization": thaw_json(self.authorization),
            "result": thaw_json(self.result),
        }


def guard_action(action: Action | None, expected_version: int, allowed_states):
    """CAS preconditions for future *named* operations; not a state setter or dispatch permit."""
    if action is None:
        raise ActionError(ActionCode.NOT_FOUND)
    if type(expected_version) is not int or action.version != expected_version:
        raise ActionError(ActionCode.STALE_VERSION)
    if action.state not in allowed_states:
        raise ActionError(ActionCode.NOT_EXECUTABLE)


def safe_result(state, receipt=None):
    code, message = {
        ActionState.Succeeded: ("EXECUTION_SUCCEEDED", "The demo note effect was confirmed."),
        ActionState.Failed: (
            "EXECUTION_CONFIRMED_FAILED",
            "The external effect was confirmed not to have occurred.",
        ),
        ActionState.Unknown: (
            "EXECUTION_OUTCOME_UNKNOWN",
            "The external effect outcome is unknown.",
        ),
    }[state]
    return {"code": code, "message": message, "receipt_reference": receipt}
