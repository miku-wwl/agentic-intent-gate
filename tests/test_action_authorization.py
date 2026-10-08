import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from threading import Barrier
from uuid import uuid4

import pytest
from action_helpers import cmd, inject_fault
from run_helpers import database_rows

from agentic_intent_gate.adapters.local_capability import CapabilityVerifier, LocalDemoAuthorization
from agentic_intent_gate.adapters.sqlite_action_execution import SQLiteExecutionRepository
from agentic_intent_gate.adapters.sqlite_v2_runs import SQLiteV2RunRepository
from agentic_intent_gate.application.action_compiler import ActionCompiler
from agentic_intent_gate.domain.action import ActionCode, ActionError, ActionState


def test_authorization_separate_exact_grant_replay_no_run_churn(auth_env):
    e = auth_env
    before_run = database_rows(e.runs.path)
    command = cmd(e.action)
    result = e.repo.authorize(command, e.principal, e.policy)
    assert result.action.state is ActionState.Authorized and result.action.version == 2
    assert result.action.authorization["subject_id"] == e.principal.subject_id
    assert result.action.authorization["policy_revision"] == e.policy.policy_revision
    assert result.action.authorization["authorized_at"] == result.action.updated_at
    assert result.action.result is None and not e.ledger()["action_attempts"]
    grant = e.ledger()["action_authorizations"][0]
    assert grant[3] == e.action.spec.spec_digest
    before = e.ledger()
    assert e.repo.authorize(command, e.principal, e.policy).replayed
    assert e.repo.get(e.action.action_id) == result.action
    assert e.ledger() == before and database_rows(e.runs.path) == before_run
    with pytest.raises(ActionError) as exc:
        e.repo.authorize(replace(command, expected_action_version=2), e.principal, e.policy)
    assert exc.value.code is ActionCode.IDEMPOTENCY_CONFLICT
    with pytest.raises(ActionError):
        e.repo.authorize(cmd(result.action), e.principal, e.policy)
    assert e.ledger() == before


@pytest.mark.parametrize("same_key", [True, False])
def test_twenty_authorization_commands_one_grant(auth_env, same_key):
    e = auth_env
    barrier, key = Barrier(20), str(uuid4())

    def call(index):
        barrier.wait(timeout=5)
        try:
            return e.repo.authorize(
                cmd(e.action, key=key if same_key else str(uuid4())), e.principal, e.policy
            )
        except ActionError:
            return None

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(call, range(20)))
    assert len([r for r in results if r and not r.replayed]) == 1
    assert len(e.ledger()["action_authorizations"]) == 1
    assert len(e.ledger()["action_events"]) == 2
    assert e.repo.get(e.action.action_id).version == 2


@pytest.mark.parametrize(
    "stage",
    [
        "INSERT INTO action_authorizations",
        "UPDATE actions",
        "INSERT INTO action_events",
        "INSERT INTO action_command_receipts",
        "COMMIT",
    ],
)
def test_authorization_fault_rolls_back_all_facts(auth_env, monkeypatch, stage):
    e = auth_env
    before = e.ledger()
    with monkeypatch.context() as patch:
        inject_fault(patch, stage)
        with pytest.raises(ActionError) as exc:
            e.repo.authorize(cmd(e.action), e.principal, e.policy)
        assert exc.value.code is ActionCode.DATABASE_UNAVAILABLE
    assert e.ledger() == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("subject_id", "other"),
        ("spec_digest", "0" * 64),
        ("policy_revision", "other-v1"),
        ("authorization_id", str(uuid4())),
        ("authorized_at", "2020-01-01T00:00:00.000000Z"),
    ],
)
def test_grant_tamper_fails_read_and_claim(auth_env, field, value):
    e = auth_env
    action = e.repo.authorize(cmd(e.action), e.principal, e.policy).action
    with closing(sqlite3.connect(e.runs.path)) as connection:
        connection.execute(f"UPDATE action_authorizations SET {field}=?", (value,))
        connection.commit()
    before = e.ledger()
    with pytest.raises(ActionError):
        e.repo.get(action.action_id)
    with pytest.raises(ActionError):
        e.repo.claim(cmd(action), e.principal, e.policy)
    assert e.ledger() == before


def test_retired_binding_and_changed_policy_do_not_reauthorize_or_claim(auth_env):
    e = auth_env
    action = e.repo.authorize(cmd(e.action), e.principal, e.policy).action
    current = ActionCompiler(
        e.runs.validator, (replace(e.verifier.binding, binding_revision="demo-binding-v2"),)
    )
    reopened = SQLiteExecutionRepository(
        SQLiteV2RunRepository(e.runs.path, e.runs.validator), current
    )
    before = e.ledger()
    assert reopened.get(action.action_id) == action
    for operation in (reopened.authorize, reopened.claim):
        with pytest.raises(ActionError) as exc:
            operation(cmd(action), e.principal, e.policy)
        assert exc.value.code is ActionCode.TOOL_NOT_BOUND
    changed = LocalDemoAuthorization(e.verifier, policy_revision="demo-policy-v2")
    with pytest.raises(ActionError) as exc:
        e.repo.claim(cmd(action), e.principal, changed)
    assert exc.value.code is ActionCode.NOT_AUTHORIZED
    assert e.ledger() == before


@pytest.mark.parametrize("version", [2, 4])
def test_stale_pending_authorization_no_changes(auth_env, version):
    e = auth_env
    before = e.ledger()
    with pytest.raises(ActionError) as exc:
        e.repo.authorize(cmd(e.action, version), e.principal, e.policy)
    assert exc.value.code is ActionCode.STALE_VERSION
    assert e.ledger() == before


def test_pending_never_claims(auth_env):
    e = auth_env
    before = e.ledger()
    with pytest.raises(ActionError) as exc:
        e.repo.claim(cmd(e.action), e.principal, e.policy)
    assert exc.value.code is ActionCode.NOT_EXECUTABLE
    assert e.ledger() == before


@pytest.mark.parametrize("change", ["subject", "scope", "permission"])
def test_fresh_authority_changes_cannot_execute_existing_grant(auth_env, change):
    e = auth_env
    action = e.repo.authorize(cmd(e.action), e.principal, e.policy).action
    binding = (
        replace(e.verifier.binding, execution_scope=str(uuid4()))
        if change == "scope"
        else e.verifier.binding
    )
    verifier = CapabilityVerifier(
        e.token,
        subject_id="other" if change == "subject" else e.principal.subject_id,
        binding=binding,
        operations=("read",) if change == "permission" else e.principal.operations,
    )
    principal = verifier.verify("Bearer " + e.token)
    policy = LocalDemoAuthorization(verifier, policy_revision=e.policy.policy_revision)
    before = e.ledger()
    with pytest.raises(ActionError) as exc:
        e.repo.claim(cmd(action), principal, policy)
    assert exc.value.code is ActionCode.NOT_AUTHORIZED
    assert e.ledger() == before


def test_bounded_authorization_write_lock_is_safe(auth_env):
    e = auth_env
    before = e.ledger()
    with closing(sqlite3.connect(e.runs.path, isolation_level=None)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(ActionError) as exc:
            e.repo.authorize(cmd(e.action), e.principal, e.policy)
        assert exc.value.code is ActionCode.DATABASE_UNAVAILABLE
        connection.rollback()
    assert e.ledger() == before
