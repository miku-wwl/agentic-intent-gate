"""Verified server context and deny-by-default operation/spec permission port."""

from dataclasses import dataclass, field
from typing import Protocol

from agentic_intent_gate.domain.action import ActionCode, ActionError, CompiledSpec

OPERATIONS = frozenset({"read", "admit", "authorize", "execute"})


@dataclass(frozen=True)
class PrincipalContext:
    subject_id: str
    operations: frozenset[str]
    execution_scope: str
    _issuer: object = field(repr=False, compare=False)


class AuthorizationPort(Protocol):
    def require(self, principal: PrincipalContext, operation: str) -> None: ...
    def permit(self, principal: PrincipalContext, operation: str, spec: CompiledSpec) -> str: ...


class DenyAuthorization:
    def require(self, principal, operation):
        raise ActionError(ActionCode.NOT_AUTHORIZED)

    def permit(self, principal, operation, spec):
        self.require(principal, operation)
