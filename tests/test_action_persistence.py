"""Trusted compiler and file-backed admission, not execution or authentication evidence."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import FrozenInstanceError, replace
from threading import Barrier
from uuid import uuid4

import pytest
from run_helpers import advance, database_rows, plan_for

import agentic_intent_gate.infrastructure.persistence.sqlite_actions as sqlite_actions
from agentic_intent_gate.application.action_compiler import ActionCompiler, ToolBinding
from agentic_intent_gate.application.actions import ActionService
from agentic_intent_gate.domain.action import (
    ACTION_EDGES,
    ActionCode,
    ActionError,
    ActionState,
    guard_action,
    validate_action_edge,
)
from agentic_intent_gate.domain.approval import ApprovalChoice, ApprovalDecisionRequest
from agentic_intent_gate.domain.approval_binding import BindingCode, BindingError
from agentic_intent_gate.domain.contracts import Intent, Risk
from agentic_intent_gate.domain.run import RunState, RunStoreError
from agentic_intent_gate.domain.run_validation import canonical_json, utc_now
from agentic_intent_gate.domain.validation import ValidationFailure
from agentic_intent_gate.infrastructure.persistence.sqlite_actions import SQLiteActionRepository
from agentic_intent_gate.infrastructure.persistence.sqlite_run_repository import SQLiteRunRepository
from agentic_intent_gate.infrastructure.persistence.sqlite_v2_migration import migrate_v1_to_v2
from agentic_intent_gate.infrastructure.persistence.sqlite_v2_runs import (
    SQLiteV2RunRepository,
    binding_for,
    linked_decision,
)
from agentic_intent_gate.ports.action_repository import AdmissionCommand


@pytest.fixture
def runs(tmp_path, validator):
    return SQLiteV2RunRepository(tmp_path / "v2.sqlite3", validator)


@pytest.fixture
def compiler(validator):
    return ActionCompiler(
        validator,
        (
            ToolBinding(
                "append_service_note",
                "1",
                "demo-note-adapter",
                "demo-binding-v1",
                "service-demo",
                str(uuid4()),
            ),
        ),
    )


@pytest.fixture
def actions(runs, compiler):
    return SQLiteActionRepository(runs, compiler)


def command(run, key=None, subject="test-internal-subject", version=5):
    return AdmissionCommand(run.run_id, version, subject, key or str(uuid4()))


def ledger(path):
    with closing(sqlite3.connect(path)) as connection:
        return {
            table: connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
            for table in (
                "actions",
                "action_events",
                "action_command_receipts",
                "action_authorizations",
                "action_attempts",
                "approval_plan_bindings",
            )
        }


def compile_run(compiler, runs, run):
    with runs._transaction() as connection:
        loaded = runs._load(connection, run.run_id)
        decision = linked_decision(connection, loaded)
        binding = binding_for(connection, decision) if decision else None
        return compiler.compile(loaded, decision, binding)


@pytest.mark.parametrize("before", list(ActionState))
@pytest.mark.parametrize("after", list(ActionState))
def test_action_graph_all_pairs(before, after):
    if (before, after) in ACTION_EDGES:
        validate_action_edge(before, after)
    else:
        with pytest.raises(ActionError):
            validate_action_edge(before, after)


def test_future_reconciliation_is_only_a_graph_contract():
    for target in (ActionState.Succeeded, ActionState.Failed):
        validate_action_edge(ActionState.Unknown, target, reconciliation=True)
    with pytest.raises(ActionError):
        validate_action_edge(ActionState.Unknown, ActionState.Executing, reconciliation=True)


def test_compilation_deterministic_pure_and_immutable(runs, compiler):
    run = advance(runs, RunState.Approved, Risk.SIDE_EFFECT)
    before = database_rows(runs.path), ledger(runs.path)
    first, second = compile_run(compiler, runs, run), compile_run(compiler, runs, run)
    assert first == second and first.spec_digest == second.spec_digest
    assert first.normalized_arguments["note"] == "note 😀"
    assert before == (database_rows(runs.path), ledger(runs.path))
    with pytest.raises(TypeError):
        first.normalized_arguments["note"] = "changed"
    with pytest.raises(FrozenInstanceError):
        first.adapter_id = "untrusted"
    assert not hasattr(first, "effect_key")  # generated once by admission, not pure compilation


def test_admission_replay_reopen_versions_and_pure_queries(actions, runs, compiler):
    run = advance(runs, RunState.Approved, Risk.SIDE_EFFECT)
    service = ActionService(actions)
    assert service.list_for_run(run.run_id) == ()
    request = command(run)
    run_before = database_rows(runs.path)
    result = service.admit(request)
    assert result.created and not result.replayed
    assert result.action.state is ActionState.Pending and result.action.version == 1
    assert runs.get(run.run_id).version == 5
    before = ledger(runs.path)
    replay = service.admit(request)
    assert replay.replayed and replay.action == result.action and not replay.created
    reopened = SQLiteActionRepository(SQLiteV2RunRepository(runs.path, runs.validator), compiler)
    assert reopened.get(result.action.action_id) == result.action
    assert reopened.list_for_run(run.run_id) == (result.action,)
    assert reopened.admit(request).action.effect_key == result.action.effect_key
    assert before == ledger(runs.path) and run_before == database_rows(runs.path)
    assert ledger(runs.path)["action_authorizations"] == ledger(runs.path)["action_attempts"] == []
    assert set(result.to_dict()) == {"run_id", "actions", "replayed"}
    assert (
        "effect_key" not in result.action.snapshot()
        and "spec_digest" not in result.action.snapshot()
    )
    guard_action(result.action, 1, {ActionState.Pending})
    with pytest.raises(ActionError) as exc:
        guard_action(result.action, 5, {ActionState.Pending})
    assert exc.value.code is ActionCode.STALE_VERSION
    with pytest.raises(ActionError):
        guard_action(result.action, 1, {ActionState.Authorized})
    with pytest.raises(ActionError):
        guard_action(None, 1, {ActionState.Pending})


def test_same_key_different_body_conflict_before_cas(actions, runs):
    run = advance(runs, RunState.Approved, Risk.SIDE_EFFECT)
    request = command(run)
    actions.admit(request)
    before = ledger(runs.path)
    with pytest.raises(ActionError) as exc:
        actions.admit(replace(request, expected_run_version=4))
    assert exc.value.code is ActionCode.IDEMPOTENCY_CONFLICT
    with pytest.raises(RunStoreError):
        actions.admit(command(run, version=4))
    assert before == ledger(runs.path)


def test_different_key_and_subject_same_run_existing_identical(actions, runs):
    run = advance(runs, RunState.Approved, Risk.SIDE_EFFECT)
    first = actions.admit(command(run))
    key = str(uuid4())
    for subject in ("other-subject", "third-subject"):
        result = actions.admit(command(run, key=key, subject=subject))
        assert result.action == first.action and not result.created and not result.replayed
    data = ledger(runs.path)
    assert len(data["actions"]) == len(data["action_events"]) == 1
    assert len(data["action_command_receipts"]) == 3


def test_same_key_different_runs_distinct_intentional_operations(actions, runs):
    first = advance(runs, RunState.Approved, Risk.SIDE_EFFECT)
    second = advance(runs, RunState.Approved, Risk.SIDE_EFFECT)
    key = str(uuid4())
    one, two = actions.admit(command(first, key)), actions.admit(command(second, key))
    assert one.action.action_id != two.action.action_id
    assert one.action.effect_key != two.action.effect_key
    assert one.action.spec.plan_digest == two.action.spec.plan_digest
    assert one.action.spec.spec_digest != two.action.spec.spec_digest


@pytest.mark.parametrize("state", [state for state in RunState if state is not RunState.Approved])
def test_all_ineligible_run_states(actions, runs, state):
    risk = (
        Risk.SIDE_EFFECT
        if state in {RunState.Rejected, RunState.AwaitingApproval}
        else Risk.READ_ONLY
    )
    run = advance(runs, state, risk)
    before = ledger(runs.path), database_rows(runs.path)
    with pytest.raises(ActionError):
        actions.admit(command(run, version=run.version))
    assert before == (ledger(runs.path), database_rows(runs.path))


@pytest.mark.parametrize("shape", ["maintenance", "read-plus-note", "two-notes"])
def test_whole_plan_unsupported_never_partial(actions, runs, shape):
    plan = plan_for(
        runs.validator, Risk.APPROVAL_REQUIRED if shape == "maintenance" else Risk.SIDE_EFFECT
    ).to_dict()
    if shape == "read-plus-note":
        plan["steps"].insert(0, plan_for(runs.validator).to_dict()["steps"][0])
    elif shape == "two-notes":
        other = json.loads(json.dumps(plan["steps"][0]))
        other["arguments"]["note"] = "second distinct note"
        plan["steps"].append(other)
    run = runs.create(str(uuid4()), Intent("Unsupported whole proposal"), utc_now())
    runs.start_generation(run.run_id, 1)
    runs.save_validated_plan(run.run_id, 2, runs.validator.parse_plan(canonical_json(plan)))
    runs.route_validated_plan(run.run_id, 3)
    run = runs.decide_approval(run.run_id, ApprovalDecisionRequest(ApprovalChoice.APPROVE, 4))
    with pytest.raises(ActionError) as exc:
        actions.admit(command(run))
    assert exc.value.code is ActionCode.PLAN_NOT_EXECUTABLE
    assert ledger(runs.path)["actions"] == []


def test_missing_executable_binding_is_not_metadata_permission(runs):
    run = advance(runs, RunState.Approved, Risk.SIDE_EFFECT)
    repo = SQLiteActionRepository(runs, ActionCompiler(runs.validator))
    with pytest.raises(ActionError) as exc:
        repo.admit(command(run))
    assert exc.value.code is ActionCode.TOOL_NOT_BOUND
    assert ledger(runs.path)["actions"] == []


@pytest.mark.parametrize("same_key", [True, False])
def test_concurrent_admission_independent_connections(actions, runs, compiler, same_key):
    run = advance(runs, RunState.Approved, Risk.SIDE_EFFECT)
    peers = [
        SQLiteActionRepository(SQLiteV2RunRepository(runs.path, runs.validator), compiler)
        for _ in range(2)
    ]
    key = str(uuid4())
    commands = [command(run, key), command(run, key if same_key else str(uuid4()))]
    barrier = Barrier(2)

    def admit(pair):
        repo, request = pair
        barrier.wait(timeout=5)
        return repo.admit(request)

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(admit, zip(peers, commands, strict=True)))
    assert results[0].action == results[1].action
    assert sum(r.created for r in results) == 1
    assert sum(r.replayed for r in results) == int(same_key)
    data = ledger(runs.path)
    assert len(data["actions"]) == len(data["action_events"]) == 1
    assert len(data["action_command_receipts"]) == (1 if same_key else 2)


@pytest.mark.parametrize(
    "statement,after",
    [
        ("INSERT INTO actions", False),
        ("INSERT INTO actions", True),
        ("INSERT INTO action_events", False),
        ("INSERT INTO action_events", True),
        ("INSERT INTO action_command_receipts", False),
        ("INSERT INTO action_command_receipts", True),
        ("COMMIT", False),
    ],
)
def test_admission_failure_atomic(actions, runs, monkeypatch, statement, after):
    run = advance(runs, RunState.Approved, Risk.SIDE_EFFECT)
    before = ledger(runs.path), database_rows(runs.path)

    class Fault(sqlite3.Connection):
        def execute(self, text, params=()):
            if text.startswith(statement) and not after:
                raise sqlite3.OperationalError("private SQL fault")
            result = super().execute(text, params)
            if text.startswith(statement) and after:
                raise sqlite3.OperationalError("private after SQL fault")
            return result

        def commit(self):
            if statement == "COMMIT":
                raise sqlite3.OperationalError("private commit fault")
            return super().commit()

    monkeypatch.setattr(
        sqlite_actions,
        "open_existing",
        lambda path: sqlite3.connect(path, isolation_level=None, factory=Fault),
    )
    with pytest.raises(ActionError) as exc:
        actions.admit(command(run))
    assert exc.value.code is ActionCode.DATABASE_UNAVAILABLE and "private" not in str(exc.value)
    assert before == (ledger(runs.path), database_rows(runs.path))


def test_uncertain_commit_lookup_replay_not_guessed_success(actions, runs, monkeypatch):
    run = advance(runs, RunState.Approved, Risk.SIDE_EFFECT)
    request = command(run)

    class Uncertain(sqlite3.Connection):
        def commit(self):
            super().commit()
            raise sqlite3.OperationalError("commit confirmation lost")

    with monkeypatch.context() as patch:
        patch.setattr(
            sqlite_actions,
            "open_existing",
            lambda path: sqlite3.connect(path, isolation_level=None, factory=Uncertain),
        )
        with pytest.raises(ActionError):
            actions.admit(request)
    found = actions.list_for_run(run.run_id)
    assert len(found) == 1
    before = ledger(runs.path)
    assert actions.admit(request).action == found[0]
    assert before == ledger(runs.path)


@pytest.mark.parametrize("mutation", ["plan", "missing", "digest", "legacy", "revision"])
def test_tamper_after_admission_denies_replay_and_queries(actions, runs, mutation):
    run = advance(runs, RunState.Approved, Risk.SIDE_EFFECT)
    request = command(run)
    action = actions.admit(request).action
    with closing(sqlite3.connect(runs.path)) as connection:
        connection.execute("PRAGMA ignore_check_constraints=ON")
        if mutation == "plan":
            value = run.plan.to_dict()
            value["steps"][0]["arguments"]["note"] = "Semantically valid but never approved B"
            runs.validator.parse_plan(canonical_json(value))
            connection.execute(
                "UPDATE runs SET validated_plan_json=? WHERE run_id=?",
                (canonical_json(value), run.run_id),
            )
        else:
            connection.execute(
                {
                    "missing": "DELETE FROM approval_plan_bindings",
                    "digest": "UPDATE approval_plan_bindings SET approval_plan_digest='"
                    + "0" * 64
                    + "'",
                    "legacy": (
                        "UPDATE approval_plan_bindings SET binding_status='LEGACY_UNBOUND', "
                        "identity_revision=NULL, approval_plan_digest=NULL, bound_at=NULL"
                    ),
                    "revision": (
                        "UPDATE approval_plan_bindings SET identity_revision='approval-plan-v2'"
                    ),
                }[mutation]
            )
        connection.commit()
    before = ledger(runs.path), database_rows(runs.path)
    for operation in (
        lambda: actions.admit(request),
        lambda: actions.get(action.action_id),
        lambda: actions.list_for_run(run.run_id),
    ):
        with pytest.raises(BindingError):
            operation()
    with pytest.raises(RunStoreError):
        SQLiteV2RunRepository(runs.path, runs.validator)
    assert before == (ledger(runs.path), database_rows(runs.path))


def test_legacy_admission_denied_new_separate_run_eligible(tmp_path, validator, compiler):
    path = tmp_path / "old.sqlite3"
    old = SQLiteRunRepository(path, validator)
    legacy = advance(old, RunState.Approved, Risk.SIDE_EFFECT)
    migrate_v1_to_v2(path, tmp_path / "backup.sqlite3", validator, owners_stopped=True)
    runs = SQLiteV2RunRepository(path, validator)
    actions = SQLiteActionRepository(runs, compiler)
    before = ledger(path), database_rows(path)
    with pytest.raises(BindingError) as exc:
        actions.admit(command(legacy))
    assert exc.value.code is BindingCode.LEGACY
    assert runs.get(legacy.run_id) == legacy
    assert before == (ledger(path), database_rows(path))
    new = advance(runs, RunState.Approved, Risk.SIDE_EFFECT)
    assert actions.admit(command(new)).created
    assert new.run_id != legacy.run_id and runs.get(legacy.run_id) == legacy


@pytest.mark.parametrize(
    "field,value",
    [
        ("adapter_id", "evil-adapter"),
        ("binding_revision", "other-v1"),
        ("execution_scope", str(uuid4())),
        ("compiler_revision", "unknown-v2"),
        ("plan_digest", "0" * 64),
        ("spec_digest", "0" * 64),
        ("normalized_arguments_json", '{"note":"altered","service_id":"service-demo"}'),
        ("approved_run_version", 4),
        ("step_index", 1),
        ("effect_key", str(uuid4())),
    ],
)
def test_action_spec_tampering_fail_closed(actions, runs, compiler, field, value):
    run = advance(runs, RunState.Approved, Risk.SIDE_EFFECT)
    action = actions.admit(command(run)).action
    with closing(sqlite3.connect(runs.path)) as connection:
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(f"UPDATE actions SET {field}=?", (value,))
        connection.commit()
    with pytest.raises((ActionError, RunStoreError)):
        actions.get(action.action_id)
    with pytest.raises(RunStoreError):
        SQLiteV2RunRepository(runs.path, runs.validator)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"expected_run_version":true}',
        b'{"expected_run_version":5.0}',
        b'{"expected_run_version":5,"adapter_id":"evil"}',
        b'{"expected_run_version":5,"effect_key":"evil"}',
        b'{"expected_run_version":5,"subject_id":"evil"}',
        b'{"expected_run_version":5,"expected_run_version":5}',
    ],
)
def test_admission_strict_body(raw):
    with pytest.raises((ValidationFailure, ActionError)):
        AdmissionCommand.parse(
            str(uuid4()), raw, subject_id="trusted-internal", idempotency_key=str(uuid4())
        )


@pytest.mark.parametrize(
    "change",
    [
        "event_missing",
        "event_digest",
        "event_payload",
        "receipt_missing",
        "receipt_digest",
        "receipt_resource",
        "receipt_operation",
        "future_state",
    ],
)
def test_ledger_tampering_startup_and_query_denied(actions, runs, change):
    run = advance(runs, RunState.Approved, Risk.SIDE_EFFECT)
    action = actions.admit(command(run)).action
    with closing(sqlite3.connect(runs.path)) as connection:
        connection.execute("PRAGMA ignore_check_constraints=ON")
        statements = {
            "event_missing": "DELETE FROM action_events",
            "event_digest": "UPDATE action_events SET spec_digest='" + "0" * 64 + "'",
            "event_payload": "UPDATE action_events SET safe_payload_json='{}'",
            "receipt_missing": "DELETE FROM action_command_receipts",
            "receipt_digest": "UPDATE action_command_receipts SET body_digest='" + "0" * 64 + "'",
            "receipt_resource": "UPDATE action_command_receipts SET resource_id='"
            + str(uuid4())
            + "'",
            "receipt_operation": "UPDATE action_command_receipts SET operation='execute'",
            "future_state": "UPDATE actions SET state='Authorized', version=2",
        }
        connection.execute(statements[change])
        connection.commit()
    before = ledger(runs.path)
    with pytest.raises((ActionError, RunStoreError)):
        actions.get(action.action_id)
    with pytest.raises(RunStoreError):
        SQLiteV2RunRepository(runs.path, runs.validator)
    assert before == ledger(runs.path)


def test_bounded_write_lock_no_partial_admission(actions, runs):
    import time

    run = advance(runs, RunState.Approved, Risk.SIDE_EFFECT)
    before = ledger(runs.path)
    with closing(sqlite3.connect(runs.path, isolation_level=None)) as blocker:
        blocker.execute("BEGIN IMMEDIATE")
        start = time.monotonic()
        with pytest.raises(ActionError) as exc:
            actions.admit(command(run))
        assert exc.value.code is ActionCode.DATABASE_UNAVAILABLE
        assert time.monotonic() - start < 2
        blocker.rollback()
    assert before == ledger(runs.path)


def test_toctou_writer_cannot_change_checked_plan(actions, runs, compiler, monkeypatch):
    run = advance(runs, RunState.Approved, Risk.SIDE_EFFECT)
    original = compiler.compile
    blocked = []

    def compile_guarded(self, *args):
        assert self is compiler
        result = original(*args)
        with closing(sqlite3.connect(runs.path, timeout=0.01)) as outsider:
            value = run.plan.to_dict()
            value["steps"][0]["arguments"]["note"] = "Unapproved B"
            with pytest.raises(sqlite3.OperationalError):
                outsider.execute(
                    "UPDATE runs SET validated_plan_json=? WHERE run_id=?",
                    (canonical_json(value), run.run_id),
                )
            blocked.append(True)
        return result

    monkeypatch.setattr(ActionCompiler, "compile", compile_guarded)
    result = actions.admit(command(run))
    assert blocked and result.action.spec.normalized_arguments["note"] == "note 😀"
    assert runs.get(run.run_id) == run


def test_no_unique_run_constraint_only_run_step(actions):
    with actions._transaction() as connection:
        unique_sets = []
        for index in connection.execute("PRAGMA index_list('actions')"):
            if index["unique"]:
                unique_sets.append(
                    tuple(
                        row["name"]
                        for row in connection.execute(f"PRAGMA index_info('{index['name']}')")
                    )
                )
        assert ("run_id",) not in unique_sets and ("run_id", "step_index") in unique_sets


def test_untrusted_input_strings_are_inert_data(actions, runs):
    value = plan_for(runs.validator, Risk.SIDE_EFFECT).to_dict()
    note = "../outside ; Remove-Item * <script>alert(1)</script> http://example.invalid"
    value["steps"][0]["arguments"]["note"] = note
    run = runs.create(str(uuid4()), Intent("Text is not tool authority"), utc_now())
    runs.start_generation(run.run_id, 1)
    runs.save_validated_plan(run.run_id, 2, runs.validator.parse_plan(canonical_json(value)))
    runs.route_validated_plan(run.run_id, 3)
    approved = runs.decide_approval(run.run_id, ApprovalDecisionRequest(ApprovalChoice.APPROVE, 4))
    action = actions.admit(command(approved)).action
    assert action.spec.normalized_arguments["note"] == note
    event = ledger(runs.path)["action_events"][0]
    assert note not in str(event)


def test_queries_validate_but_never_compile_new_spec(actions, runs, compiler, monkeypatch):
    run = advance(runs, RunState.Approved, Risk.SIDE_EFFECT)
    action = actions.admit(command(run)).action
    before = ledger(runs.path), database_rows(runs.path)

    def forbidden(*args):
        raise AssertionError("A query must never call the new-spec compilation entry point")

    monkeypatch.setattr(ActionCompiler, "compile", forbidden)
    assert actions.get(action.action_id) == action
    assert actions.list_for_run(run.run_id) == (action,)
    assert before == (ledger(runs.path), database_rows(runs.path))
