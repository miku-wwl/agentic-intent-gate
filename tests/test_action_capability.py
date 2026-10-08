import secrets
from dataclasses import FrozenInstanceError, replace
from uuid import uuid4

import pytest

from agentic_intent_gate.adapters.local_capability import (
    CapabilityVerifier,
    LocalDemoAuthorization,
    provision_capability,
)
from agentic_intent_gate.application.action_compiler import ToolBinding
from agentic_intent_gate.domain.action import ActionCode, ActionError
from agentic_intent_gate.ports.authorization import DenyAuthorization, PrincipalContext


def capability(operations=("read", "admit", "authorize", "execute")):
    binding = ToolBinding(
        "append_service_note",
        "1",
        "demo-note-adapter",
        "demo-binding-v1",
        "service-demo",
        str(uuid4()),
    )
    token = secrets.token_hex(32)
    verifier = CapabilityVerifier(
        token, subject_id="local-demo", binding=binding, operations=operations
    )
    return token, verifier, LocalDemoAuthorization(verifier, policy_revision="demo-policy-v1")


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "LOCAL_USER",
        "Bearer admin",
        "bearer x",
        "Bearer " + "0" * 64,
        "Bearer " + "a" * 65,
    ],
)
def test_missing_invalid_credentials(header):
    _, verifier, _ = capability()
    with pytest.raises(ActionError) as exc:
        verifier.verify(header)
    assert exc.value.code is ActionCode.AUTHENTICATION_REQUIRED


def test_unconfigured_default_denies():
    with pytest.raises(ActionError):
        CapabilityVerifier().verify("Bearer " + secrets.token_hex(32))
    with pytest.raises(ActionError):
        DenyAuthorization().require(None, "execute")


def test_verified_immutable_server_context_not_body_claim():
    token, verifier, policy = capability(("read",))
    principal = verifier.verify("Bearer " + token)
    policy.require(principal, "read")
    assert token not in repr(principal) and token not in repr(verifier)
    with pytest.raises(FrozenInstanceError):
        principal.subject_id = "admin"
    for forged in (
        None,
        {"subject_id": "local-demo"},
        PrincipalContext("local-demo", principal.operations, principal.execution_scope, object()),
        replace(principal, subject_id="admin"),
        replace(principal, operations=frozenset({"execute"})),
    ):
        with pytest.raises(ActionError):
            policy.require(forged, "read")
    with pytest.raises(ActionError):
        policy.require(principal, "execute")


def test_private_provisioning_is_explicit_and_never_overwrites(private_capability_path):
    path = private_capability_path
    provision_capability(path)
    assert len(bytes.fromhex(path.read_text())) == 32
    with pytest.raises(FileExistsError):
        provision_capability(path)
