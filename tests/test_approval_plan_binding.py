"""File-backed ADR-014 foundation; no Action implementation or model calls needed."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from pathlib import Path

import pytest
from run_helpers import advance, database_rows, plan_for

from agentic_intent_gate.adapters import sqlite_v2_migration, sqlite_v2_runs
from agentic_intent_gate.adapters.sqlite_run_repository import SQLiteRunRepository
from agentic_intent_gate.adapters.sqlite_v2_migration import migrate_v1_to_v2
from agentic_intent_gate.adapters.sqlite_v2_runs import (
    SQLiteV2RunRepository,
    binding_for,
    linked_decision,
)
from agentic_intent_gate.domain.approval import ApprovalChoice, ApprovalDecisionRequest
from agentic_intent_gate.domain.approval_binding import (
    ApprovalBinding,
    BindingCode,
    BindingError,
    digest,
    plan_digest,
)
from agentic_intent_gate.domain.contracts import Risk
from agentic_intent_gate.domain.run import RunState, RunStoreError
from agentic_intent_gate.domain.run_validation import canonical_json
from agentic_intent_gate.domain.validation import ValidationFailure


@pytest.fixture
def v2(tmp_path, validator):
    return SQLiteV2RunRepository(tmp_path / "v2.sqlite3", validator)


def sql(path, statement, params=()):
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(statement, params)
        connection.commit()


def rows(path, table):
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()


def test_exact_canonical_inputs(validator):
    plan = plan_for(validator, Risk.SIDE_EFFECT)
    raw = plan.to_dict()
    reverse = {k: raw[k] for k in reversed(raw)}
    assert plan_digest(plan, validator) == plan_digest(
        validator.parse_plan(json.dumps(reverse)), validator
    )
    assert plan_digest(plan, validator) == digest(
        {"identity_revision": "canonical-plan-v1", "plan": raw}
    )
    for changed in ("note 😀 ", "note 😃", "note e\u0301", "note é"):
        other = deepcopy(raw)
        other["steps"][0]["arguments"]["note"] = changed
        assert plan_digest(plan, validator) != plan_digest(
            validator.parse_plan(json.dumps(other)), validator
        )


@pytest.mark.parametrize("choice", list(ApprovalChoice))
def test_new_decision_bound_and_reads_pure(v2, choice):
    run = advance(v2, RunState.AwaitingApproval, Risk.SIDE_EFFECT)
    after = v2.decide_approval(run.run_id, ApprovalDecisionRequest(choice, 4))
    with v2._transaction() as connection:
        decision = linked_decision(connection, after)
        binding = binding_for(connection, decision)
        expected = {
            "identity_revision": "approval-plan-v1",
            "run_id": run.run_id,
            "decision_id": decision.decision_id,
            "decision": choice.value,
            "run_version_before": 4,
            "run_version_after": 5,
            "decision_created_at": decision.created_at,
            "plan_identity_revision": "canonical-plan-v1",
            "plan_digest": plan_digest(run.plan, v2.validator),
        }
        assert binding.approval_plan_digest == digest(expected)
        assert binding == ApprovalBinding.bound(decision, after.plan, v2.validator)
    before = database_rows(v2.path), rows(v2.path, "approval_plan_bindings")
    assert SQLiteV2RunRepository(v2.path, v2.validator).get(run.run_id) == after
    for version in (4, 5, 3):
        with pytest.raises(RunStoreError):
            v2.decide_approval(run.run_id, ApprovalDecisionRequest(choice, version))
    assert before == (database_rows(v2.path), rows(v2.path, "approval_plan_bindings"))


@pytest.mark.parametrize(
    "choices", [("APPROVE", "APPROVE"), ("REJECT", "REJECT"), ("APPROVE", "REJECT")]
)
def test_concurrent_decisions_one_winner(v2, choices):
    run = advance(v2, RunState.AwaitingApproval, Risk.SIDE_EFFECT)
    peers = [SQLiteV2RunRepository(v2.path, v2.validator) for _ in choices]

    def decide(pair):
        repo, choice = pair
        try:
            return repo.decide_approval(
                run.run_id, ApprovalDecisionRequest(ApprovalChoice(choice), 4)
            )
        except RunStoreError:
            return None

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(decide, zip(peers, choices, strict=True)))
    assert sum(result is not None for result in results) == 1
    assert (
        len(rows(v2.path, "approval_decisions"))
        == len(rows(v2.path, "approval_plan_bindings"))
        == 1
    )
    assert len(rows(v2.path, "audit_events")) == 5


@pytest.mark.parametrize(
    "mutation", ["note", "goal", "target", "revision", "risk", "order", "count"]
)
@pytest.mark.parametrize("state", [RunState.Approved, RunState.Rejected])
def test_plan_tampering_denies_read_startup_reapproval(v2, mutation, state):
    if mutation == "order":
        waiting = advance(v2, RunState.PlanGenerating)
        original = plan_for(v2.validator, Risk.SIDE_EFFECT).to_dict()
        second = deepcopy(original["steps"][0])
        second["arguments"]["note"] = "Second distinct approved note"
        original["steps"].append(second)
        v2.save_validated_plan(waiting.run_id, 2, v2.validator.parse_plan(canonical_json(original)))
        v2.route_validated_plan(waiting.run_id, 3)
        choice = ApprovalChoice.APPROVE if state is RunState.Approved else ApprovalChoice.REJECT
        run = v2.decide_approval(waiting.run_id, ApprovalDecisionRequest(choice, 4))
    else:
        run = advance(v2, state, Risk.SIDE_EFFECT)
    value = run.plan.to_dict()
    if mutation in {"note", "goal"}:
        if mutation == "note":
            value["steps"][0]["arguments"]["note"] = "Plan B remains valid 😀"
        else:
            value["goal"] = "Different valid goal"
        v2.validator.parse_plan(canonical_json(value))  # decisive semantically valid A -> B
    elif mutation == "target":
        value["target"] = value["steps"][0]["arguments"]["service_id"] = "other-service"
    elif mutation == "revision":
        value["steps"][0]["tool_version"] = "2"
    elif mutation == "risk":
        value["risk_class"] = "APPROVAL_REQUIRED"
    elif mutation == "order":
        value["steps"].reverse()
        v2.validator.parse_plan(canonical_json(value))
    else:
        read = {
            "tool_name": "get_service_record",
            "tool_version": "1",
            "arguments": {"service_id": "service-demo"},
        }
        value["steps"].insert(0, read)
        v2.validator.parse_plan(canonical_json(value))
    with closing(sqlite3.connect(v2.path)) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            "UPDATE runs SET validated_plan_json=? WHERE run_id=?",
            (canonical_json(value), run.run_id),
        )
        connection.commit()
    before = database_rows(v2.path), rows(v2.path, "approval_plan_bindings")
    with pytest.raises(RunStoreError):
        v2.get(run.run_id)
    with pytest.raises(RunStoreError):
        SQLiteV2RunRepository(v2.path, v2.validator)
    with pytest.raises(RunStoreError):
        v2.decide_approval(run.run_id, ApprovalDecisionRequest(ApprovalChoice.APPROVE, 4))
    assert before == (database_rows(v2.path), rows(v2.path, "approval_plan_bindings"))


@pytest.mark.parametrize(
    "change,code",
    [
        ("missing", BindingCode.MISSING),
        ("null", BindingCode.MISSING),
        ("revision", BindingCode.UNSUPPORTED),
        ("format", BindingCode.UNSUPPORTED),
        ("status", BindingCode.UNSUPPORTED),
        ("contradiction", BindingCode.UNSUPPORTED),
        ("digest", BindingCode.MISMATCH),
        ("time", BindingCode.MISMATCH),
    ],
)
def test_binding_error_classification(v2, change, code):
    run = advance(v2, RunState.Approved, Risk.SIDE_EFFECT)
    with closing(sqlite3.connect(v2.path)) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        statement = {
            "missing": "DELETE FROM approval_plan_bindings",
            "null": "UPDATE approval_plan_bindings SET approval_plan_digest=NULL",
            "revision": "UPDATE approval_plan_bindings SET identity_revision='approval-plan-v2'",
            "format": (
                "UPDATE approval_plan_bindings SET approval_plan_digest=upper(approval_plan_digest)"
            ),
            "status": "UPDATE approval_plan_bindings SET binding_status='OTHER'",
            "contradiction": "UPDATE approval_plan_bindings SET binding_status='LEGACY_UNBOUND'",
            "digest": "UPDATE approval_plan_bindings SET approval_plan_digest='" + "0" * 64 + "'",
            "time": "UPDATE approval_plan_bindings SET bound_at='2020-01-01T00:00:00.000000Z'",
        }[change]
        connection.execute(statement)
        connection.commit()
    with pytest.raises(BindingError) as exc, v2._transaction() as connection:
        v2._load(connection, run.run_id)
    assert exc.value.code == code
    with pytest.raises(RunStoreError):
        v2.get(run.run_id)


def test_copied_binding_denied_and_duplicate_constrained(v2):
    first = advance(v2, RunState.Approved, Risk.SIDE_EFFECT)
    second = advance(v2, RunState.Approved, Risk.SIDE_EFFECT)
    with v2._transaction() as connection:
        one = binding_for(connection, linked_decision(connection, first))
        two = binding_for(connection, linked_decision(connection, second))
        assert one.approval_plan_digest != two.approval_plan_digest
    sql(
        v2.path,
        "UPDATE approval_plan_bindings SET approval_plan_digest=? WHERE decision_id=?",
        (one.approval_plan_digest, two.decision_id),
    )
    with pytest.raises(RunStoreError):
        v2.get(second.run_id)
    with closing(sqlite3.connect(v2.path)) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO approval_plan_bindings VALUES (?, ?, ?, ?, ?)", tuple(vars(one).values())
        )


@pytest.mark.parametrize(
    "stage",
    [
        "UPDATE runs",
        "INSERT INTO approval_decisions",
        "INSERT INTO approval_plan_bindings",
        "INSERT INTO audit_events",
        "COMMIT",
    ],
)
def test_approval_fault_rolls_back(v2, monkeypatch, stage):
    run = advance(v2, RunState.AwaitingApproval, Risk.SIDE_EFFECT)
    before = database_rows(v2.path)

    class Fault(sqlite3.Connection):
        def execute(self, statement, params=()):
            if statement.startswith(stage):
                raise sqlite3.OperationalError("private fault detail")
            return super().execute(statement, params)

        def commit(self):
            if stage == "COMMIT":
                raise sqlite3.OperationalError("private commit detail")
            return super().commit()

    monkeypatch.setattr(
        sqlite_v2_runs,
        "open_existing",
        lambda path: sqlite3.connect(path, isolation_level=None, factory=Fault),
    )
    with pytest.raises(RunStoreError) as exc:
        v2.decide_approval(run.run_id, ApprovalDecisionRequest(ApprovalChoice.APPROVE, 4))
    assert "private" not in str(exc.value)
    assert database_rows(v2.path) == before and rows(v2.path, "approval_plan_bindings") == []


def test_migration_all_states_and_wal_backup(tmp_path, validator):
    path, backup = tmp_path / "old.sqlite3", tmp_path / "private-backup.sqlite3"
    old = SQLiteRunRepository(path, validator)
    runs = []
    for state in RunState:
        risk = (
            Risk.SIDE_EFFECT
            if state in {RunState.AwaitingApproval, RunState.Approved, RunState.Rejected}
            else Risk.READ_ONLY
        )
        runs.append(advance(old, state, risk))
    # Keep committed WAL pages alive while backup runs; this connection is not an app owner.
    with closing(sqlite3.connect(path)) as holder:
        holder.execute("SELECT * FROM runs").fetchall()
        extra = advance(old, RunState.Approved, Risk.SIDE_EFFECT)
        assert Path(str(path) + "-wal").is_file()
        before = database_rows(path)
        ddl = rows_schema(path)
        with pytest.raises(RunStoreError):
            SQLiteV2RunRepository(path, validator)
        migrate_v1_to_v2(path, backup, validator, owners_stopped=True)
    assert before == database_rows(path) == database_rows(backup)
    assert ddl == {k: v for k, v in rows_schema(path).items() if k in ddl}
    assert SQLiteRunRepository(backup, validator).get(extra.run_id) == extra
    with pytest.raises(RunStoreError):
        SQLiteRunRepository(path, validator)
    new = SQLiteV2RunRepository(path, validator)
    assert [new.get(r.run_id) for r in runs] == runs
    assert len(rows_schema(path)) == 9
    assert all(
        row[1:] == ("LEGACY_UNBOUND", None, None, None)
        for row in rows(path, "approval_plan_bindings")
    )
    assert rows(path, "actions") == []
    awaiting = next(r for r in runs if r.state == RunState.AwaitingApproval)
    approved = new.decide_approval(
        awaiting.run_id, ApprovalDecisionRequest(ApprovalChoice.APPROVE, 4)
    )
    with new._transaction() as connection:
        assert (
            binding_for(connection, linked_decision(connection, approved)).binding_status
            == "BOUND_V1"
        )
    with pytest.raises(RunStoreError):
        new.decide_approval(extra.run_id, ApprovalDecisionRequest(ApprovalChoice.APPROVE, 5))


def rows_schema(path):
    with closing(sqlite3.connect(path)) as connection:
        return dict(connection.execute("SELECT name, sql FROM sqlite_schema WHERE type='table'"))


@pytest.mark.parametrize(
    "stage",
    [
        "CREATE TABLE approval_plan_bindings",
        "CREATE TABLE actions",
        "INSERT INTO approval_plan_bindings",
        "PRAGMA user_version = 2",
        "COMMIT",
    ],
)
def test_migration_fault_atomic(tmp_path, validator, monkeypatch, stage):
    path, backup = tmp_path / "old.sqlite3", tmp_path / "backup.sqlite3"
    old = SQLiteRunRepository(path, validator)
    advance(old, RunState.Approved, Risk.SIDE_EFFECT)
    before, ddl = database_rows(path), rows_schema(path)
    original = sqlite_v2_migration.open_existing

    class Fault(sqlite3.Connection):
        def execute(self, statement, params=()):
            if statement.startswith(stage):
                raise sqlite3.OperationalError("migration fault")
            return super().execute(statement, params)

        def commit(self):
            if stage == "COMMIT":
                raise sqlite3.OperationalError("commit fault")
            return super().commit()

    calls = 0

    def open_fault(path):
        nonlocal calls
        calls += 1
        return (
            sqlite3.connect(path, isolation_level=None, factory=Fault)
            if calls == 1
            else original(path)
        )

    monkeypatch.setattr(sqlite_v2_migration, "open_existing", open_fault)
    with pytest.raises(RunStoreError):
        migrate_v1_to_v2(path, backup, validator, owners_stopped=True)
    assert database_rows(path) == before and rows_schema(path) == ddl
    assert SQLiteRunRepository(path, validator)
    assert database_rows(backup) == before


@pytest.mark.parametrize("kind", ["future", "partial", "extra", "v1"])
def test_v2_schema_fail_closed(tmp_path, validator, kind):
    path = tmp_path / "bad.sqlite3"
    if kind == "v1":
        SQLiteRunRepository(path, validator)
    else:
        SQLiteV2RunRepository(path, validator)
        sql(
            path,
            {
                "future": "PRAGMA user_version=3",
                "partial": "DROP TABLE action_events",
                "extra": "CREATE TABLE unexpected(value TEXT) STRICT",
            }[kind],
        )
    before = rows_schema(path)
    with pytest.raises(RunStoreError):
        SQLiteV2RunRepository(path, validator)
    assert rows_schema(path) == before


@pytest.mark.parametrize(
    "raw",
    [
        b'{"decision":"APPROVE","expected_version":4,"plan_digest":"x"}',
        b'{"decision":"APPROVE","expected_version":4,"binding_status":"BOUND_V1"}',
    ],
)
def test_client_cannot_supply_binding(raw):
    with pytest.raises(ValidationFailure):
        ApprovalDecisionRequest.parse(raw)


@pytest.mark.parametrize("choice", list(ApprovalChoice))
def test_migrated_waiting_new_bound_choice(tmp_path, validator, choice):
    path = tmp_path / "old.sqlite3"
    old = SQLiteRunRepository(path, validator)
    waiting = advance(old, RunState.AwaitingApproval, Risk.SIDE_EFFECT)
    migrate_v1_to_v2(path, tmp_path / "backup.sqlite3", validator, owners_stopped=True)
    new = SQLiteV2RunRepository(path, validator)
    after = new.decide_approval(waiting.run_id, ApprovalDecisionRequest(choice, 4))
    with new._transaction() as connection:
        decision = linked_decision(connection, after)
        binding_for(connection, decision).verify(decision, after.plan, validator)


def test_approval_uncertain_commit_no_guessed_success(v2, monkeypatch):
    waiting = advance(v2, RunState.AwaitingApproval, Risk.SIDE_EFFECT)

    class Uncertain(sqlite3.Connection):
        def commit(self):
            super().commit()
            raise sqlite3.OperationalError("lost commit confirmation")

    with monkeypatch.context() as patch:
        patch.setattr(
            sqlite_v2_runs,
            "open_existing",
            lambda path: sqlite3.connect(path, isolation_level=None, factory=Uncertain),
        )
        with pytest.raises(RunStoreError):
            v2.decide_approval(waiting.run_id, ApprovalDecisionRequest(ApprovalChoice.APPROVE, 4))
    assert v2.get(waiting.run_id).state == RunState.Approved
    assert len(rows(v2.path, "approval_plan_bindings")) == 1


@pytest.mark.parametrize("kind", ["offline", "backup_exists", "future", "partial", "history"])
def test_migration_preflight_fail_closed(tmp_path, validator, kind):
    path, backup = tmp_path / "old.sqlite3", tmp_path / "backup.sqlite3"
    old = SQLiteRunRepository(path, validator)
    advance(old, RunState.Approved, Risk.SIDE_EFFECT)
    if kind == "backup_exists":
        SQLiteRunRepository(backup, validator)
    elif kind == "future":
        sql(path, "PRAGMA user_version=7")
    elif kind == "partial":
        sql(path, "CREATE TABLE extra(value TEXT) STRICT")
    elif kind == "history":
        sql(path, "DELETE FROM audit_events WHERE run_version=5")
    before = database_rows(path), rows_schema(path)
    with pytest.raises(RunStoreError):
        migrate_v1_to_v2(path, backup, validator, owners_stopped=kind != "offline")
    assert before == (database_rows(path), rows_schema(path))


@pytest.mark.parametrize("kind", ["mixed_null", "unknown_revision", "wrong_status", "orphan"])
def test_database_binding_constraints(v2, kind):
    run = advance(v2, RunState.Approved, Risk.SIDE_EFFECT)
    with closing(sqlite3.connect(v2.path)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError):
            if kind == "orphan":
                connection.execute(
                    "INSERT INTO approval_plan_bindings(decision_id,binding_status) "
                    "VALUES ('missing','LEGACY_UNBOUND')"
                )
            else:
                connection.execute(
                    {
                        "mixed_null": "UPDATE approval_plan_bindings SET bound_at=NULL",
                        "unknown_revision": (
                            "UPDATE approval_plan_bindings SET identity_revision='v2'"
                        ),
                        "wrong_status": "UPDATE approval_plan_bindings SET binding_status='other'",
                    }[kind]
                )
    assert v2.get(run.run_id) == run


@pytest.mark.parametrize(
    "raw", ['{"goal":NaN}', '{"goal":1.0}', '{"goal":1,"goal":2}', '{"goal":"\\ud800"}']
)
def test_identity_strict_parser_rejects_invalid_json(validator, raw):
    with pytest.raises(ValidationFailure):
        validator.parse_plan(raw)


@pytest.mark.parametrize("choice", list(ApprovalChoice))
async def test_v2_approval_http_unchanged_and_no_action_routes(
    v2, validator, provider_factory, choice
):
    import httpx

    from agentic_intent_gate.adapters.local_http import create_app
    from agentic_intent_gate.application.generate_plan import PlanningService
    from agentic_intent_gate.application.runs import RunService

    provider = provider_factory()
    planning = PlanningService(provider, validator)
    app = create_app(planning, run_service=RunService(v2, planning))
    waiting = advance(v2, RunState.AwaitingApproval, Risk.SIDE_EFFECT)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        url = f"/api/runs/{waiting.run_id}"
        invalid = await client.post(
            url + "/approval",
            json={
                "decision": choice.value,
                "expected_version": 4,
                "approval_plan_digest": "0" * 64,
            },
        )
        assert invalid.status_code == 400
        response = await client.post(
            url + "/approval",
            json={
                "decision": choice.value,
                "expected_version": 4,
                "reason": "Still the old request shape",
            },
        )
        assert response.status_code == 200 and response.json() == v2.get(waiting.run_id).to_dict()
        assert (await client.get(url)).json() == response.json()
        assert (await client.get(url + "/actions")).status_code == 404
        assert (
            await client.post(url + "/actions", json={"expected_run_version": 5})
        ).status_code == 404
        assert provider.calls == 0
        value = waiting.plan.to_dict()
        value["goal"] = "Legal but not approved"
        sql(
            v2.path,
            "UPDATE runs SET validated_plan_json=? WHERE run_id=?",
            (canonical_json(value), waiting.run_id),
        )
        denied = await client.get(url)
        assert denied.status_code == 503
        assert denied.json()["error"]["code"] == "DATABASE_UNAVAILABLE"


def test_decision_mismatch_history_precedes_binding_classification(v2):
    run = advance(v2, RunState.Approved, Risk.SIDE_EFFECT)
    sql(v2.path, "UPDATE approval_decisions SET decision='REJECT'")
    sql(v2.path, "DELETE FROM approval_plan_bindings")
    with pytest.raises(RunStoreError), v2._transaction() as connection:
        v2._load(connection, run.run_id)


def test_orphan_companion_startup_denied(v2):
    from uuid import uuid4

    sql(
        v2.path,
        "INSERT INTO approval_plan_bindings(decision_id,binding_status) "
        "VALUES (?, 'LEGACY_UNBOUND')",
        (str(uuid4()),),
    )
    with pytest.raises(RunStoreError):
        SQLiteV2RunRepository(v2.path, v2.validator)
