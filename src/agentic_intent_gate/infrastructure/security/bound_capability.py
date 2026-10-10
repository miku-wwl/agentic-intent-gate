"""Frozen, one-binding local capability policy; not production user authentication."""

from dataclasses import dataclass, field

from agentic_intent_gate.application.action_compiler import ToolBinding
from agentic_intent_gate.domain.action import ActionCode, ActionError, identifier
from agentic_intent_gate.domain.contracts import Risk
from agentic_intent_gate.infrastructure.security.local_capability import CapabilityVerifier
from agentic_intent_gate.ports.authorization import PrincipalContext


@dataclass(frozen=True, init=False)
class BoundCapabilityAuthorization:
    binding: ToolBinding
    policy_revision: str
    subject_id: str
    operations: frozenset[str]
    _issuer: object = field(repr=False)

    def __init__(self, verifier: CapabilityVerifier, *, policy_revision: str):
        if (
            type(verifier) is not CapabilityVerifier
            or verifier._digest is None
            or type(verifier.binding) is not ToolBinding
            or not identifier(policy_revision)
        ):
            raise ActionError(ActionCode.NOT_AUTHORIZED)
        for key, value in {
            "binding": verifier.binding,
            "policy_revision": policy_revision,
            "subject_id": verifier.subject_id,
            "operations": verifier.operations,
            "_issuer": verifier._issuer,
        }.items():
            object.__setattr__(self, key, value)

    def require(self, principal, operation):
        if (
            not isinstance(principal, PrincipalContext)
            or principal._issuer is not self._issuer
            or principal.subject_id != self.subject_id
            or principal.execution_scope != self.binding.execution_scope
            or principal.operations != self.operations
            or operation not in self.operations
        ):
            raise ActionError(ActionCode.NOT_AUTHORIZED)

    def permit(self, principal, operation, spec):
        self.require(principal, operation)
        if (
            not self.binding.matches(spec)
            or spec.risk_class is not Risk.SIDE_EFFECT
            or spec.compiler_revision != self.binding.compiler_revision
        ):
            raise ActionError(ActionCode.NOT_AUTHORIZED)
        return self.policy_revision
