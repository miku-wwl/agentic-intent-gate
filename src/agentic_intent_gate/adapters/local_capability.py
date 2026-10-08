"""One private operator capability, not human identity or a general login system."""

import hashlib
import hmac
import re
import secrets
from pathlib import Path

from agentic_intent_gate.adapters.sqlite_v2_migration import private_backup_file
from agentic_intent_gate.application.action_compiler import ToolBinding
from agentic_intent_gate.domain.action import ActionCode, ActionError, identifier
from agentic_intent_gate.ports.authorization import OPERATIONS, PrincipalContext


def provision_capability(path):
    """Explicit operator-only preparation. New ACL-private file, 256 random bits, no output."""
    path = Path(path).resolve()
    private_backup_file(path)
    with path.open("w", encoding="ascii", newline="") as stream:
        stream.write(secrets.token_hex(32))


class CapabilityVerifier:
    def __init__(self, token=None, *, subject_id=None, binding=None, operations=()):
        self._issuer = object()
        self._digest = None
        self.subject_id, self.binding = subject_id, binding
        self.operations = frozenset(operations)
        if token is not None:
            if (
                not isinstance(token, str)
                or re.fullmatch(r"[0-9a-f]{64}", token) is None
                or not identifier(subject_id, limit=128)
                or not isinstance(binding, ToolBinding)
                or not self.operations <= OPERATIONS
            ):
                raise ActionError(ActionCode.AUTHENTICATION_REQUIRED)
            self._digest = hashlib.sha256(token.encode("ascii")).digest()

    def verify(self, header):
        if (
            self._digest is None
            or not isinstance(header, str)
            or re.fullmatch(r"Bearer [0-9a-f]{64}", header) is None
        ):
            raise ActionError(ActionCode.AUTHENTICATION_REQUIRED)
        candidate = hashlib.sha256(header[7:].encode("ascii")).digest()
        if not hmac.compare_digest(candidate, self._digest):
            raise ActionError(ActionCode.AUTHENTICATION_REQUIRED)
        return PrincipalContext(
            self.subject_id, self.operations, self.binding.execution_scope, self._issuer
        )


class LocalDemoAuthorization:
    def __init__(self, verifier, *, policy_revision):
        if not identifier(policy_revision):
            raise ActionError(ActionCode.NOT_AUTHORIZED)
        self.verifier, self.policy_revision = verifier, policy_revision

    def require(self, principal, operation):
        if (
            not isinstance(principal, PrincipalContext)
            or self.verifier._digest is None
            or principal._issuer is not self.verifier._issuer
            or principal.subject_id != self.verifier.subject_id
            or principal.execution_scope != self.verifier.binding.execution_scope
            or principal.operations != self.verifier.operations
            or operation not in principal.operations
        ):
            raise ActionError(ActionCode.NOT_AUTHORIZED)

    def permit(self, principal, operation, spec):
        self.require(principal, operation)
        b = self.verifier.binding
        if (spec.tool_name, spec.tool_version, spec.target, spec.risk_class.value) != (
            "append_service_note",
            "1",
            "service-demo",
            "SIDE_EFFECT",
        ) or (spec.adapter_id, spec.binding_revision, spec.execution_scope) != (
            b.adapter_id,
            b.binding_revision,
            b.execution_scope,
        ):
            raise ActionError(ActionCode.NOT_AUTHORIZED)
        return self.policy_revision
