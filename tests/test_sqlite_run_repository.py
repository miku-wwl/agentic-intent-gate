"""Disk persistence, corruption, rollback, SQLite constraints and real connection races."""

import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from uuid import uuid4

import pytest
from run_helpers import advance, database_rows, plan_for

from agentic_intent_gate.adapters import sqlite_run_repository as adapter
from agentic_intent_gate.adapters import sqlite_schema
from agentic_intent_gate.adapters.configuration import ConfigurationError, Settings
from agentic_intent_gate.adapters.sqlite_run_repository import SQLiteRunRepository
from agentic_intent_gate.domain.approval import ApprovalChoice, ApprovalDecisionRequest
from agentic_intent_gate.domain.contracts import Intent, Risk
from agentic_intent_gate.domain.run import RunErrorCode, RunState, RunStoreError
from agentic_intent_gate.domain.run_validation import canonical_json, utc_now


def independent_connection(repository):
    connection = sqlite_schema.open_existing(repository.path)
    sqlite_schema.configure_connection(connection)
    return connection


def test_frozen_ddl_exact_structure_and_per_connection_pragmas(run_repository):
    document = (
        Path(__file__).resolve().parents[1] / "docs/day2-state-architecture-freeze.md"
    ).read_text(encoding="utf-8")
    ddl = document.split("```sql\n")[1].split("```", 1)[0]
    assert [sqlite_schema.normalize_ddl(part) for part in ddl.split(";") if part.strip()] == [
        sqlite_schema.normalize_ddl(value) for value in sqlite_schema.TABLES.values()
    ]
    for _ in range(2):
        connection = independent_connection(run_repository)
        try:
            assert connection.isolation_level is None
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
            for pragma, value in {
                "foreign_keys": 1,
                "busy_timeout": 250,
                "journal_mode": "wal",
                "synchronous": 2,
            }.items():
                assert connection.execute(f"PRAGMA {pragma}").fetchone()[0] == value
            tables = connection.execute("PRAGMA table_list").fetchall()
            assert {row[1] for row in tables if row[5] == 1} == set(sqlite_schema.TABLES)
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        finally:
            connection.close()


@pytest.mark.parametrize("state", list(RunState))
def test_file_backed_reopen_every_state(run_repository, validator, state):
    risk = (
        Risk.SIDE_EFFECT
        if state in {RunState.AwaitingApproval, RunState.Approved, RunState.Rejected}
        else Risk.READ_ONLY
    )
    run = advance(run_repository, state, risk)
    before = database_rows(run_repository.path)
    reopened = SQLiteRunRepository(run_repository.path, validator)
    assert reopened.get(run.run_id) == run
    assert database_rows(run_repository.path) == before
    if run.plan:
        with closing(sqlite3.connect(run_repository.path)) as connection, connection:
            raw = connection.execute("SELECT validated_plan_json FROM runs").fetchone()[0]
        assert raw == canonical_json(run.plan.to_dict())
        assert len(raw.encode("utf-8")) <= 16384


@pytest.mark.parametrize(
    "sql,params",
    [
        ("UPDATE runs SET version=?", ("not an integer",)),
        ("UPDATE runs SET version=0", ()),
        ("UPDATE runs SET intent=?", ("界" * 3000,)),
        ("UPDATE runs SET intent=?", ("a" * 8193,)),
        ("UPDATE runs SET validated_plan_json=?", ("invalid json",)),
        (
            "UPDATE runs SET validated_plan_json=?",
            ('{"risk_class":"READ_ONLY","x":"' + "x" * 16384 + '"}',),
        ),
        ("UPDATE runs SET risk_class='SIDE_EFFECT'", ()),
        ("UPDATE runs SET state='PlanGenerated'", ()),
        ("UPDATE audit_events SET run_version=0", ()),
        ("UPDATE audit_events SET event_type='EXECUTED'", ()),
        ("UPDATE audit_events SET safe_payload_json='[]'", ()),
        ("UPDATE audit_events SET run_id=?", (str(uuid4()),)),
        (
            "INSERT INTO audit_events SELECT NULL,run_id,run_version,event_type,created_at,"
            "safe_payload_json "
            "FROM audit_events",
            (),
        ),
    ],
)
def test_real_sqlite_constraints(run_repository, sql, params):
    advance(run_repository)
    before = database_rows(run_repository.path)
    connection = independent_connection(run_repository)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(sql, params)
    finally:
        connection.close()
    assert database_rows(run_repository.path) == before


@pytest.mark.parametrize(
    "sql,params",
    [
        ("UPDATE approval_decisions SET reason=?", ("😀" * 1025,)),
        ("UPDATE approval_decisions SET actor_type='REMOTE_USER'", ()),
        ("UPDATE approval_decisions SET decision='ALLOW'", ()),
        ("UPDATE approval_decisions SET run_version_after=9", ()),
        ("UPDATE approval_decisions SET run_id=?", (str(uuid4()),)),
        (
            "INSERT INTO approval_decisions SELECT ?,run_id,decision,reason,actor_type,created_at,"
            "run_version_before,run_version_after FROM approval_decisions",
            (str(uuid4()),),
        ),
    ],
)
def test_decision_fk_unique_and_bounds(run_repository, sql, params):
    advance(run_repository, RunState.Approved, Risk.SIDE_EFFECT)
    connection = independent_connection(run_repository)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(sql, params)
    finally:
        connection.close()


@pytest.mark.parametrize(
    "corruption",
    [
        "json",
        "canonical",
        "risk",
        "semantic",
        "state",
        "version",
        "calendar",
        "error",
        "audit-missing",
        "audit-extra-key",
        "audit-edge",
        "audit-time",
        "audit-gap",
        "decision-missing",
        "decision-link",
        "decision-control",
        "orphan-decision",
        "orphan-audit",
    ],
)
def test_corrupt_data_fails_closed_on_read_and_startup(run_repository, validator, corruption):
    run = advance(run_repository, RunState.Approved, Risk.SIDE_EFFECT)
    with closing(sqlite3.connect(run_repository.path)) as connection, connection:
        connection.execute("PRAGMA ignore_check_constraints=ON")
        if corruption in {"json", "canonical", "semantic"}:
            value = run.plan.to_dict()
            if corruption == "semantic":
                value["steps"][0]["arguments"]["service_id"] = "other"
            raw = "{" if corruption == "json" else json.dumps(value)
            connection.execute("UPDATE runs SET validated_plan_json=?", (raw,))
        elif corruption == "risk":
            connection.execute("UPDATE runs SET risk_class='APPROVAL_REQUIRED'")
        elif corruption == "state":
            connection.execute("UPDATE runs SET state='PlanGenerated'")
        elif corruption == "version":
            connection.execute("UPDATE runs SET version=4")
        elif corruption == "calendar":
            connection.execute("UPDATE runs SET updated_at='2026-02-30T00:00:00.000000Z'")
        elif corruption == "error":
            connection.execute("UPDATE runs SET last_error_message='private error'")
        elif corruption == "audit-missing":
            connection.execute("DELETE FROM audit_events WHERE run_version=2")
        elif corruption == "audit-extra-key":
            connection.execute(
                "UPDATE audit_events SET safe_payload_json=json_set("
                "safe_payload_json,'$.secret','bad') WHERE run_version=1"
            )
        elif corruption == "audit-edge":
            connection.execute(
                "UPDATE audit_events SET event_type='RECOVERY_ROUTED' WHERE run_version=5"
            )
        elif corruption == "audit-time":
            connection.execute(
                "UPDATE audit_events SET created_at='2000-01-01T00:00:00.000000Z' "
                "WHERE run_version=3"
            )
        elif corruption == "audit-gap":
            connection.execute("UPDATE audit_events SET run_version=8 WHERE run_version=2")
        elif corruption == "decision-missing":
            connection.execute("DELETE FROM approval_decisions")
        elif corruption == "decision-link":
            connection.execute("UPDATE approval_decisions SET decision='REJECT'")
        elif corruption == "decision-control":
            connection.execute("UPDATE approval_decisions SET reason=?", ("\x00",))
        elif corruption == "orphan-decision":
            connection.execute("UPDATE approval_decisions SET run_id=?", (str(uuid4()),))
        elif corruption == "orphan-audit":
            connection.execute(
                "UPDATE audit_events SET run_id=? WHERE run_version=2", (str(uuid4()),)
            )
    corrupted = database_rows(run_repository.path)
    with pytest.raises(RunStoreError) as caught:
        run_repository.get(run.run_id)
    assert caught.value.code == RunErrorCode.DATABASE_UNAVAILABLE
    with pytest.raises(RunStoreError) as caught:
        SQLiteRunRepository(run_repository.path, validator)
    assert caught.value.code == RunErrorCode.DATABASE_UNAVAILABLE
    assert database_rows(run_repository.path) == corrupted


@pytest.mark.parametrize("change", ["version", "partial", "extra", "trigger", "index", "ddl"])
def test_unsupported_schema_is_not_changed(tmp_path, validator, change):
    path = tmp_path / "unsupported.sqlite3"
    if change == "partial":
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("CREATE TABLE runs(x TEXT)")
    else:
        SQLiteRunRepository(path, validator)
        with closing(sqlite3.connect(path)) as connection, connection:
            if change == "version":
                connection.execute("PRAGMA user_version=2")
            elif change == "extra":
                connection.execute("CREATE TABLE extra(x TEXT)")
            elif change == "trigger":
                connection.execute(
                    "CREATE TRIGGER unwanted AFTER INSERT ON runs BEGIN SELECT 1; END"
                )
            elif change == "index":
                connection.execute("CREATE INDEX unwanted ON runs(state)")
            else:
                connection.execute("PRAGMA writable_schema=ON")
                connection.execute(
                    "UPDATE sqlite_schema SET sql=replace(sql,'8192','8193') WHERE name='runs'"
                )
    before = path.read_bytes()
    with pytest.raises(RunStoreError) as caught:
        SQLiteRunRepository(path, validator)
    assert caught.value.code == RunErrorCode.DATABASE_SCHEMA_UNSUPPORTED
    assert path.read_bytes() == before


def fault_connections(monkeypatch, *, statement=None, commit_mode=None, before_statement=False):
    original = sqlite_schema.open_existing

    class FaultConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if before_statement and statement and sql.startswith(statement):
                raise sqlite3.OperationalError("private injected SQL failure")
            result = super().execute(sql, parameters)
            if statement and sql.startswith(statement):
                raise sqlite3.OperationalError("private injected SQL failure")
            return result

        def commit(self):
            if commit_mode == "before":
                raise sqlite3.OperationalError("private commit failure")
            super().commit()
            if commit_mode == "after":
                raise sqlite3.OperationalError("private unconfirmed commit")

    def connect(path):
        return sqlite3.connect(
            path.as_uri() + "?mode=rw",
            uri=True,
            isolation_level=None,
            factory=FaultConnection,
            timeout=0.25,
        )

    monkeypatch.setattr(adapter, "open_existing", connect)
    return original


@pytest.mark.parametrize("choice", list(ApprovalChoice))
@pytest.mark.parametrize("stage", ["before-update", "update", "decision", "event", "commit"])
def test_approval_rolls_back_at_each_write_boundary(run_repository, monkeypatch, stage, choice):
    run = advance(run_repository, RunState.AwaitingApproval, Risk.SIDE_EFFECT)
    before = database_rows(run_repository.path)
    with monkeypatch.context() as patch:
        fault_connections(
            patch,
            statement={
                "before-update": "UPDATE runs",
                "update": "UPDATE runs",
                "decision": "INSERT INTO approval_decisions",
                "event": "INSERT INTO audit_events",
            }.get(stage),
            commit_mode="before" if stage == "commit" else None,
            before_statement=stage == "before-update",
        )
        with pytest.raises(RunStoreError) as caught:
            run_repository.decide_approval(run.run_id, ApprovalDecisionRequest(choice, 4))
        assert caught.value.code == RunErrorCode.DATABASE_UNAVAILABLE
        assert "private" not in str(caught.value) and caught.value.run_id == run.run_id
    assert database_rows(run_repository.path) == before
    assert run_repository.get(run.run_id) == run


def test_unconfirmed_commit_has_no_invented_success_or_replay(run_repository, monkeypatch):
    run = advance(run_repository, RunState.AwaitingApproval, Risk.SIDE_EFFECT)
    with monkeypatch.context() as patch:
        fault_connections(patch, commit_mode="after")
        with pytest.raises(RunStoreError):
            run_repository.decide_approval(
                run.run_id, ApprovalDecisionRequest(ApprovalChoice.APPROVE, 4)
            )
    assert run_repository.get(run.run_id).state == RunState.Approved
    rows = database_rows(run_repository.path)
    assert len(rows["approval_decisions"]) == 1 and len(rows["audit_events"]) == 5


def test_creation_and_initialization_rollback(tmp_path, validator, monkeypatch, run_repository):
    with monkeypatch.context() as patch:
        fault_connections(patch, statement="INSERT INTO audit_events")
        with pytest.raises(RunStoreError):
            run_repository.create(str(uuid4()), Intent("Read"), utc_now())
    assert all(not rows for rows in database_rows(run_repository.path).values())
    original = sqlite3.connect

    class InitializationFailure(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql.startswith("CREATE TABLE audit_events"):
                raise sqlite3.OperationalError("DDL failure")
            return super().execute(sql, parameters)

    path = tmp_path / "init-failure.sqlite3"
    with monkeypatch.context() as patch:
        patch.setattr(
            sqlite3, "connect", lambda *a, **k: original(*a, factory=InitializationFailure, **k)
        )
        with pytest.raises(RunStoreError):
            SQLiteRunRepository(path, validator)
    with closing(original(path)) as connection, connection:
        assert connection.execute("SELECT * FROM sqlite_schema").fetchall() == []
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0


def test_real_two_connection_approval_race(run_repository, monkeypatch):
    run = advance(run_repository, RunState.AwaitingApproval, Risk.SIDE_EFFECT)
    barrier = threading.Barrier(2)
    connections = []

    class RacingConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql == "BEGIN IMMEDIATE":
                barrier.wait(timeout=5)
            return super().execute(sql, parameters)

    def connect(path):
        connection = sqlite3.connect(
            path, isolation_level=None, timeout=0.25, factory=RacingConnection
        )
        connections.append(connection)
        return connection

    def decide(choice):
        try:
            return run_repository.decide_approval(run.run_id, ApprovalDecisionRequest(choice, 4))
        except RunStoreError as exc:
            return exc

    with monkeypatch.context() as patch:
        patch.setattr(adapter, "open_existing", connect)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(decide, [ApprovalChoice.APPROVE, ApprovalChoice.REJECT]))
    assert len(connections) == 2 and connections[0] is not connections[1]
    winners = [r for r in results if not isinstance(r, RunStoreError)]
    losers = [r for r in results if isinstance(r, RunStoreError)]
    assert len(winners) == len(losers) == 1
    assert losers[0].code == RunErrorCode.STALE_RUN_VERSION
    final = run_repository.get(run.run_id)
    assert final == winners[0] and final.version == 5
    rows = database_rows(run_repository.path)
    assert len(rows["approval_decisions"]) == 1
    events = [row for row in rows["audit_events"] if row[3] in {"APPROVED", "REJECTED"}]
    assert len(events) == 1 and events[0][3] == final.state.value.upper()
    with pytest.raises(RunStoreError) as caught:
        run_repository.decide_approval(
            run.run_id, ApprovalDecisionRequest(ApprovalChoice.APPROVE, 4)
        )
    assert caught.value.code == RunErrorCode.STALE_RUN_VERSION
    assert database_rows(run_repository.path) == rows


@pytest.mark.parametrize("operation", ["create", "start", "save", "route", "approve", "reject"])
def test_bounded_held_database_lock_changes_nothing(run_repository, operation):
    state = {
        "create": RunState.IntentReceived,
        "start": RunState.IntentReceived,
        "save": RunState.PlanGenerating,
        "route": RunState.PlanValidated,
    }.get(operation, RunState.AwaitingApproval)
    run = advance(run_repository, state, Risk.SIDE_EFFECT)
    operations = {
        "create": lambda: run_repository.create(str(uuid4()), Intent("Read"), utc_now()),
        "start": lambda: run_repository.start_generation(run.run_id, 1),
        "save": lambda: run_repository.save_validated_plan(
            run.run_id, 2, plan_for(run_repository.validator, Risk.SIDE_EFFECT)
        ),
        "route": lambda: run_repository.route_validated_plan(run.run_id, 3),
        "approve": lambda: run_repository.decide_approval(
            run.run_id, ApprovalDecisionRequest(ApprovalChoice.APPROVE, 4)
        ),
        "reject": lambda: run_repository.decide_approval(
            run.run_id, ApprovalDecisionRequest(ApprovalChoice.REJECT, 4)
        ),
    }
    rows = database_rows(run_repository.path)
    blocker = independent_connection(run_repository)
    blocker.execute("BEGIN IMMEDIATE")
    started = time.monotonic()
    try:
        with pytest.raises(RunStoreError) as caught:
            operations[operation]()
        assert caught.value.code == RunErrorCode.DATABASE_UNAVAILABLE
        assert time.monotonic() - started < 1.5
        assert (
            run_repository.get(run.run_id) == run
        )  # WAL snapshot reads do not take the write lock.
    finally:
        blocker.rollback()
        blocker.close()
    assert database_rows(run_repository.path) == rows


@pytest.mark.parametrize(
    "path",
    [
        ":memory:",
        "file:database?mode=memory",
        "",
        ".",
        "..",
        "\\\\server\\share\\db.sqlite3",
        "//server/share/db.sqlite3",
    ],
)
def test_configuration_disallows_non_file_or_remote_databases(path):
    with pytest.raises(ConfigurationError):
        Settings("http://127.0.0.1:1", "test-model", db_path=path)


def test_clock_regression_clamps_and_conflict_ordering(run_repository):
    run = advance(run_repository, RunState.PlanGenerating)
    run_repository._clock = lambda: "2000-01-01T00:00:00.000000Z"
    validated = run_repository.save_validated_plan(
        run.run_id, 2, plan_for(run_repository.validator)
    )
    assert validated.updated_at == run.updated_at and validated.version == 3
    ready = run_repository.route_validated_plan(run.run_id, 3)
    before = database_rows(run_repository.path)
    for version, code in [
        (2, RunErrorCode.STALE_RUN_VERSION),
        (4, RunErrorCode.INVALID_RUN_TRANSITION),
    ]:
        with pytest.raises(RunStoreError) as caught:
            run_repository.start_generation(ready.run_id, version)
        assert caught.value.code == code
    with pytest.raises(RunStoreError) as caught:
        run_repository.start_generation(str(uuid4()), 1)
    assert caught.value.code == RunErrorCode.RUN_NOT_FOUND
    assert database_rows(run_repository.path) == before
