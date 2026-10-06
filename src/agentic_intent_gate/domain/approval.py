"""Versioned local human decisions; LOCAL_USER is an unauthenticated marker."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from agentic_intent_gate.domain.contracts import Risk
from agentic_intent_gate.domain.run_validation import (
    MAX_VERSION,
    check_contract,
    parse_timestamp,
    valid_uuid,
)
from agentic_intent_gate.domain.validation import REQUEST_BYTES, fail, strict_object


class ApprovalChoice(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


def requires_approval(risk: Risk) -> bool:
    if not isinstance(risk, Risk):
        fail("UNKNOWN_APPROVAL_RISK")
    return {Risk.READ_ONLY: False, Risk.SIDE_EFFECT: True, Risk.APPROVAL_REQUIRED: True}[risk]


def validate_reason(reason: str | None) -> None:
    if reason is None:
        return
    if not isinstance(reason, str) or len(reason) > 1024:
        fail("APPROVAL_REASON")
    try:
        if len(reason.encode("utf-8")) > 4096:
            fail("APPROVAL_REASON")
    except UnicodeError:
        fail("APPROVAL_REASON")
    if any((ord(c) < 32 and c not in "\t\r\n") or ord(c) == 127 for c in reason):
        fail("APPROVAL_REASON")


@dataclass(frozen=True)
class ApprovalDecisionRequest:
    decision: ApprovalChoice
    expected_version: int
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.decision, ApprovalChoice):
            fail("APPROVAL_CHOICE")
        if type(self.expected_version) is not int or not 1 <= self.expected_version <= MAX_VERSION:
            fail("APPROVAL_VERSION")
        validate_reason(self.reason)

    @classmethod
    def parse(cls, raw: bytes) -> ApprovalDecisionRequest:
        value = strict_object(raw, max_bytes=REQUEST_BYTES)
        check_contract("day2-approval.schema.json", value)
        return cls(
            ApprovalChoice(value["decision"]), value["expected_version"], value.get("reason")
        )


@dataclass(frozen=True)
class ApprovalDecision:
    decision_id: str
    run_id: str
    decision: ApprovalChoice
    reason: str | None
    actor_type: str
    created_at: str
    run_version_before: int
    run_version_after: int

    def __post_init__(self) -> None:
        if not valid_uuid(self.decision_id) or not valid_uuid(self.run_id):
            fail("APPROVAL_ID")
        if not isinstance(self.decision, ApprovalChoice) or self.actor_type != "LOCAL_USER":
            fail("APPROVAL_ACTOR_OR_CHOICE")
        validate_reason(self.reason)
        parse_timestamp(self.created_at)
        if (
            type(self.run_version_before) is not int
            or type(self.run_version_after) is not int
            or (self.run_version_before, self.run_version_after) != (4, 5)
        ):
            fail("APPROVAL_VERSIONS")
