"""ADR-014 identities. Integrity evidence is not authenticated consent or a MAC."""

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum

from agentic_intent_gate.domain.approval import ApprovalDecision
from agentic_intent_gate.domain.contracts import Plan
from agentic_intent_gate.domain.run_validation import canonical_json, parse_timestamp
from agentic_intent_gate.domain.validation import PlanValidator, ValidationFailure

PLAN_IDENTITY_REVISION = "canonical-plan-v1"
APPROVAL_IDENTITY_REVISION = "approval-plan-v1"


class BindingCode(StrEnum):
    MISSING = "APPROVAL_PLAN_BINDING_MISSING"
    LEGACY = "LEGACY_APPROVAL_UNBOUND"
    UNSUPPORTED = "APPROVAL_PLAN_BINDING_UNSUPPORTED"
    MISMATCH = "APPROVAL_PLAN_MISMATCH"


BINDING_MESSAGES = {
    BindingCode.MISSING: "The approval has no exact Plan binding.",
    BindingCode.LEGACY: "The historical approval has no exact Plan binding.",
    BindingCode.UNSUPPORTED: "The approval binding format is not supported.",
    BindingCode.MISMATCH: "The stored Plan does not match the approval binding.",
}


class BindingError(Exception):
    def __init__(self, code: BindingCode):
        self.code = code
        super().__init__(BINDING_MESSAGES[code])


def is_digest(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value) is not None


def digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def plan_digest(plan: Plan, validator: PlanValidator) -> str:
    validated = validator.parse_plan(canonical_json(plan.to_dict()))
    return digest({"identity_revision": PLAN_IDENTITY_REVISION, "plan": validated.to_dict()})


def approval_digest(decision: ApprovalDecision, content_digest: str) -> str:
    # Revalidate constructed domain values; the context has exactly these nine keys.
    ApprovalDecision(**vars(decision))
    if not is_digest(content_digest):
        raise BindingError(BindingCode.UNSUPPORTED)
    return digest(
        {
            "identity_revision": APPROVAL_IDENTITY_REVISION,
            "run_id": decision.run_id,
            "decision_id": decision.decision_id,
            "decision": decision.decision.value,
            "run_version_before": decision.run_version_before,
            "run_version_after": decision.run_version_after,
            "decision_created_at": decision.created_at,
            "plan_identity_revision": PLAN_IDENTITY_REVISION,
            "plan_digest": content_digest,
        }
    )


@dataclass(frozen=True)
class ApprovalBinding:
    decision_id: str
    binding_status: str
    identity_revision: str | None
    approval_plan_digest: str | None
    bound_at: str | None

    @classmethod
    def bound(cls, decision: ApprovalDecision, plan: Plan, validator: PlanValidator):
        return cls(
            decision.decision_id,
            "BOUND_V1",
            APPROVAL_IDENTITY_REVISION,
            approval_digest(decision, plan_digest(plan, validator)),
            decision.created_at,
        )

    def verify(
        self,
        decision: ApprovalDecision,
        plan: Plan,
        validator: PlanValidator,
        *,
        admission: bool = False,
    ) -> None:
        proof = (self.identity_revision, self.approval_plan_digest, self.bound_at)
        if self.binding_status == "BOUND_V1" and any(v is None for v in proof):
            raise BindingError(BindingCode.MISSING)
        if self.binding_status == "LEGACY_UNBOUND" and all(v is None for v in proof):
            if admission:
                raise BindingError(BindingCode.LEGACY)
            return
        if (
            self.binding_status != "BOUND_V1"
            or self.identity_revision != APPROVAL_IDENTITY_REVISION
            or not is_digest(self.approval_plan_digest)
        ):
            raise BindingError(BindingCode.UNSUPPORTED)
        try:
            parse_timestamp(self.bound_at)
        except ValidationFailure:
            raise BindingError(BindingCode.UNSUPPORTED) from None
        if (
            self.decision_id != decision.decision_id
            or self.bound_at != decision.created_at
            or self.approval_plan_digest != approval_digest(decision, plan_digest(plan, validator))
        ):
            raise BindingError(BindingCode.MISMATCH)
