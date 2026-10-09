"""Offline migration faults use retained real handles and independently committed writes."""

import asyncio
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from uuid import uuid4

import pytest
from execution_process_helpers import record_fact
from run_helpers import advance

from agentic_intent_gate.adapters import sqlite_v2_migration as migration
from agentic_intent_gate.adapters.sqlite_actions import SQLiteActionRepository
from agentic_intent_gate.adapters.sqlite_run_repository import SQLiteRunRepository
from agentic_intent_gate.adapters.sqlite_v2_runs import (
    SQLiteV2RunRepository,
    binding_for,
    linked_decision,
)
from agentic_intent_gate.adapters.sqlite_v2_schema import ADDITIONS, validate_schema
from agentic_intent_gate.application.action_compiler import ActionCompiler, ToolBinding
from agentic_intent_gate.domain.approval import ApprovalChoice, ApprovalDecisionRequest
from agentic_intent_gate.domain.approval_binding import BindingCode, BindingError
from agentic_intent_gate.domain.contracts import Risk
from agentic_intent_gate.domain.run import RUN_MESSAGES, RunErrorCode, RunState, RunStoreError
from agentic_intent_gate.ports.action_repository import AdmissionCommand


@dataclass
class Fault:
    stage: str = ""
    inspect: bool = False
    rollback: bool = False
    close: str = ""
    acquire: bool = False
    commit: str = ""
    primary: BaseException | None = None


@pytest.fixture
def handles():
    opened = []  # Strong references, including after product cleanup; no GC rescue.
    connect = sqlite3.connect

    def make(path, role, fault):
        class Connection(sqlite3.Connection):
            rollback_attempts = close_attempts = 0
            transaction_before_close = None

            def execute(self, sql, parameters=()):
                if fault.stage and sql.startswith(fault.stage):
                    if fault.stage == "INSERT INTO approval_plan_bindings":
                        # A real partial data write, still inside the migration transaction.
                        super().execute(sql + " LIMIT 1", parameters)
                    raise fault.primary or sqlite3.OperationalError("owned migration fault")
                return super().execute(sql, parameters)

            def commit(self):
                if fault.commit == "before":
                    raise sqlite3.OperationalError("owned commit-before fault")
                super().commit()
                if fault.commit == "after":
                    raise sqlite3.OperationalError("owned lost commit acknowledgement")

            @property
            def in_transaction(self):
                if fault.inspect:
                    raise sqlite3.OperationalError("owned inspection fault")
                return super().in_transaction

            def rollback(self):
                self.rollback_attempts += 1
                if fault.rollback:
                    raise sqlite3.OperationalError("owned rollback fault")
                return super().rollback()

            def close(self):
                self.close_attempts += 1
                try:
                    self.transaction_before_close = sqlite3.Connection.in_transaction.__get__(self)
                except sqlite3.ProgrammingError:
                    self.transaction_before_close = "already closed"
                if fault.close != "refuse":
                    super().close()
                if fault.close:
                    raise sqlite3.OperationalError("owned close fault")

        c = connect(path, isolation_level=None, factory=Connection)
        c.role = role
        opened.append(c)
        return c

    def install(patch, writer, *, reader=None, backup=None):
        count = 0

        def existing(path):
            nonlocal count
            count += 1
            if count == 1 and writer.acquire:
                raise sqlite3.OperationalError("owned acquisition fault")
            return make(
                path,
                "writer" if count == 1 else "reader",
                writer if count == 1 else reader or Fault(),
            )

        patch.setattr(migration, "open_existing", existing)

        def backup_connect(path, **options):
            # The schema's DDL-only reference is not a migration-owned business handle.
            if path == ":memory:":
                return connect(path, **options)
            return make(path, "backup", backup or Fault())

        patch.setattr(migration.sqlite3, "connect", backup_connect)
        return opened

    yield install, opened
    for c in opened:
        try:
            sqlite3.Connection.rollback(c)
        except sqlite3.ProgrammingError:
            pass
        finally:
            sqlite3.Connection.close(c)


def snapshot(path):
    with closing(sqlite3.connect(path)) as c:
        assert c.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
        schema = c.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY 1,2"
        ).fetchall()
        names = [r[1] for r in schema if r[0] == "table"]
        return {
            "version": c.execute("PRAGMA user_version").fetchone()[0],
            "schema": schema,
            "rows": {
                name: c.execute(f"SELECT * FROM {name} ORDER BY 1").fetchall() for name in names
            },
            "foreign_keys": {
                name: c.execute(f"PRAGMA foreign_key_list({name})").fetchall() for name in names
            },
            "strict_tables": sorted(r[1] for r in c.execute("PRAGMA table_list") if r[5]),
        }


def seeded(tmp_path, validator):
    path = tmp_path / "source.sqlite3"
    old = SQLiteRunRepository(path, validator)
    runs = [
        advance(old, state, Risk.SIDE_EFFECT)
        for state in (RunState.Approved, RunState.Rejected, RunState.AwaitingApproval)
    ]
    return path, runs, snapshot(path)


def writer_probe(path, *, expected=True):
    started = time.monotonic()
    with closing(sqlite3.connect(path, isolation_level=None, timeout=0.05)) as c:
        try:
            c.execute("BEGIN IMMEDIATE")
            c.execute("UPDATE runs SET intent=intent")
            c.commit()
            success = True
        except sqlite3.OperationalError:
            success = False
    assert success is expected
    record_fact(
        "migration-writer",
        success=success,
        expected=expected,
        writer_seconds=time.monotonic() - started,
        actual_update_commit=success,
    )


def released(path, opened):
    for c in opened:
        assert c.close_attempts >= 1
        with pytest.raises(sqlite3.ProgrammingError):
            c.execute("SELECT 1")
        record_fact(
            "migration-handle",
            role=c.role,
            close_attempts=c.close_attempts,
            rollback_attempts=c.rollback_attempts,
            transaction_before_close=c.transaction_before_close,
            strong_reference_retained=True,
            physically_closed=True,
        )
    writer_probe(path)


def migrated(path, before, runs, validator):
    after = snapshot(path)
    assert after["version"] == 2 and len(after["strict_tables"]) == 9
    assert all(after["rows"][name] == rows for name, rows in before["rows"].items())
    assert all(row in after["schema"] for row in before["schema"])
    assert all(after["foreign_keys"][name] == rows for name, rows in before["foreign_keys"].items())
    assert len(after["rows"]["approval_plan_bindings"]) == 2
    assert all(
        row[1:] == ("LEGACY_UNBOUND", None, None, None)
        for row in after["rows"]["approval_plan_bindings"]
    )
    assert all(after["rows"][name] == [] for name in ADDITIONS if name != "approval_plan_bindings")
    new = SQLiteV2RunRepository(path, validator)
    assert [new.get(run.run_id) for run in runs] == runs
    with closing(sqlite3.connect(path)) as c:
        validate_schema(c)
        c.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError):
            c.execute(
                "INSERT INTO approval_plan_bindings VALUES (?,'LEGACY_UNBOUND','bad',NULL,NULL)",
                (str(uuid4()),),
            )
    assert snapshot(path) == after
    return new


CASES = [
    ("01-clean", Fault(), True),
    ("02-before-begin", Fault(stage="PRAGMA foreign_keys = ON"), False),
    ("03-ddl-rollback", Fault(stage="CREATE TABLE actions"), False),
    ("04-ddl-rollback-throws", Fault(stage="CREATE TABLE actions", rollback=True), False),
    ("05-inspection-throws", Fault(stage="CREATE TABLE actions", inspect=True), False),
    (
        "06-rollback-close-throws",
        Fault(stage="CREATE TABLE actions", rollback=True, close="ack"),
        False,
    ),
    ("07-acquisition", Fault(acquire=True), False),
    ("08-commit-before", Fault(commit="before"), False),
    ("09-commit-after", Fault(commit="after"), True),
    ("10-close-after-success", Fault(close="ack"), True),
    ("11-explicit-retry", Fault(stage="CREATE TABLE actions"), False),
    ("12-already-v2", Fault(), False),
    ("13-future-schema", Fault(), False),
    ("14-data-midway", Fault(stage="INSERT INTO approval_plan_bindings"), False),
    ("15-legacy-classification", Fault(), True),
    ("16-fresh-reopen-e2e", Fault(), True),
]


@pytest.mark.parametrize("case,fault,committed", CASES, ids=[row[0] for row in CASES])
def test_migration_fault_matrix(tmp_path, validator, monkeypatch, handles, case, fault, committed):
    path, runs, before = seeded(tmp_path, validator)
    backup = tmp_path / "private-backup.sqlite3"
    if case.startswith("12"):
        migration.migrate_v1_to_v2(
            path, tmp_path / "initial-backup.sqlite3", validator, owners_stopped=True
        )
        before = snapshot(path)
    elif case.startswith("13"):
        with closing(sqlite3.connect(path)) as c:
            c.execute("PRAGMA user_version=7")
        before = snapshot(path)
    install, opened = handles
    success = case[:2] in {"01", "15", "16"}
    error = None
    with monkeypatch.context() as patch:
        install(patch, fault)
        if success:
            assert (
                migration.migrate_v1_to_v2(path, backup, validator, owners_stopped=True) == backup
            )
        else:
            with pytest.raises(RunStoreError) as raised:
                migration.migrate_v1_to_v2(path, backup, validator, owners_stopped=True)
            error = raised.value
            code = (
                RunErrorCode.DATABASE_SCHEMA_UNSUPPORTED
                if case[:2] in {"12", "13"}
                else RunErrorCode.DATABASE_UNAVAILABLE
            )
            assert error.code == code and str(error) == RUN_MESSAGES[code]
            assert "owned" not in str(error) and "owned" not in str(getattr(error, "__notes__", []))
    released(path, opened)
    if committed:
        new = migrated(path, before, runs, validator)
    else:
        assert snapshot(path) == before
        if case[:2] not in {"12", "13"}:
            assert [SQLiteRunRepository(path, validator).get(r.run_id) for r in runs] == runs
    if backup.exists():
        assert snapshot(backup) == before
        assert SQLiteRunRepository(backup, validator).get(runs[0].run_id) == runs[0]
    if case.startswith("11"):
        previous_backup = snapshot(backup)
        migration.migrate_v1_to_v2(
            path, tmp_path / "retry-new-backup.sqlite3", validator, owners_stopped=True
        )
        migrated(path, before, runs, validator)
        assert snapshot(backup) == previous_backup
        writer_probe(path)
    if case[:2] in {"15", "16"}:
        scope = str(uuid4())
        compiler = ActionCompiler(
            validator,
            (
                ToolBinding(
                    "append_service_note",
                    "1",
                    "demo-note-adapter",
                    "demo-binding-v1",
                    "service-demo",
                    scope,
                ),
            ),
        )
        actions = SQLiteActionRepository(new, compiler)
        unchanged = snapshot(path)
        with pytest.raises(BindingError) as denied:
            actions.admit(AdmissionCommand(runs[0].run_id, 5, "migration-test", str(uuid4())))
        assert denied.value.code == BindingCode.LEGACY
        assert snapshot(path) == unchanged and actions.list_for_run(runs[0].run_id) == ()
        if case.startswith("16"):
            approved = new.decide_approval(
                runs[2].run_id, ApprovalDecisionRequest(ApprovalChoice.APPROVE, 4)
            )
            with new._transaction() as c:
                binding_for(c, linked_decision(c, approved)).verify(
                    linked_decision(c, approved), approved.plan, validator
                )
            action = actions.admit(
                AdmissionCommand(approved.run_id, 5, "migration-test", str(uuid4()))
            ).action
            reopened = SQLiteActionRepository(SQLiteV2RunRepository(path, validator), compiler)
            assert reopened.get(action.action_id) == action
            writer_probe(path)
    record_fact(
        "migration-matrix",
        case=case,
        result="PASS",
        committed=committed,
        error_type=type(error).__name__ if error else None,
        error_code=error.code.value if error else None,
        version=snapshot(path)["version"],
        history_preserved=True,
        atomicity=True,
        orphan_or_partial_rows=False,
    )


@pytest.mark.parametrize("repetition", [1, 2])
def test_original_rollback_fault_two_fresh_writers(
    tmp_path, validator, monkeypatch, handles, repetition
):
    path, _, before = seeded(tmp_path, validator)
    install, opened = handles
    with monkeypatch.context() as patch:
        install(patch, Fault(stage="CREATE TABLE actions", rollback=True))
        with pytest.raises(RunStoreError) as raised:
            migration.migrate_v1_to_v2(
                path, tmp_path / "backup.sqlite3", validator, owners_stopped=True
            )
    assert raised.value.code == RunErrorCode.DATABASE_UNAVAILABLE
    assert opened[0].rollback_attempts == 1 and opened[0].close_attempts == 1
    assert opened[0].transaction_before_close is True
    released(path, opened)
    assert snapshot(path) == before
    record_fact("migration-original", repetition=repetition, result="PASS")


@pytest.mark.parametrize("cleanup", ["inspect", "rollback", "close"])
@pytest.mark.parametrize("primary", ["typed", "cancel"])
def test_primary_identity_survives_cleanup(
    tmp_path, validator, monkeypatch, handles, cleanup, primary
):
    path, _, before = seeded(tmp_path, validator)
    error = (
        RunStoreError(RunErrorCode.DATABASE_SCHEMA_UNSUPPORTED)
        if primary == "typed"
        else asyncio.CancelledError()
    )
    fault = Fault(stage="CREATE TABLE actions", primary=error)
    setattr(fault, cleanup, "ack" if cleanup == "close" else True)
    install, opened = handles
    with monkeypatch.context() as patch:
        install(patch, fault)
        with pytest.raises(type(error)) as raised:
            migration.migrate_v1_to_v2(
                path, tmp_path / "backup.sqlite3", validator, owners_stopped=True
            )
    assert raised.value is error
    assert "owned" not in str(getattr(error, "__notes__", []))
    released(path, opened)
    assert snapshot(path) == before


@pytest.mark.parametrize(
    "role,cleanup",
    [
        (role, cleanup)
        for role in ("reader", "backup")
        for cleanup in ("inspect", "rollback", "close")
    ],
)
def test_all_acquired_handles_cleanup_independently(
    tmp_path, validator, monkeypatch, handles, role, cleanup
):
    path, _, before = seeded(tmp_path, validator)
    error = RunStoreError(RunErrorCode.DATABASE_UNAVAILABLE)
    fault = Fault()
    setattr(fault, cleanup, "ack" if cleanup == "close" else True)
    install, opened = handles
    validate = migration.validate_rows
    count = 0

    def fail_backup(c, repository):
        nonlocal count
        count += 1
        validate(c, repository)
        if count == 2:
            # All three handles have been acquired. Give backup a real pending transaction.
            if role == "backup":
                c.execute("BEGIN IMMEDIATE")
            raise error

    with monkeypatch.context() as patch:
        install(patch, Fault(), **{role: fault})
        patch.setattr(migration, "validate_rows", fail_backup)
        with pytest.raises(RunStoreError) as raised:
            migration.migrate_v1_to_v2(
                path, tmp_path / "backup.sqlite3", validator, owners_stopped=True
            )
    assert raised.value is error
    assert {c.role for c in opened} == {"writer", "reader", "backup"}
    released(path, opened)
    assert snapshot(path) == snapshot(tmp_path / "backup.sqlite3") == before


def test_physical_close_refusal_is_not_claimed_released(tmp_path, validator, monkeypatch, handles):
    path, _, before = seeded(tmp_path, validator)
    install, opened = handles
    with monkeypatch.context() as patch:
        install(patch, Fault(stage="CREATE TABLE actions", rollback=True, close="refuse"))
        with pytest.raises(RunStoreError) as raised:
            migration.migrate_v1_to_v2(
                path, tmp_path / "backup.sqlite3", validator, owners_stopped=True
            )
    assert raised.value.code == RunErrorCode.DATABASE_UNAVAILABLE
    assert opened[0].close_attempts == opened[0].rollback_attempts == 1
    assert sqlite3.Connection.in_transaction.__get__(opened[0]) is True
    writer_probe(path, expected=False)
    sqlite3.Connection.rollback(opened[0])
    sqlite3.Connection.close(opened[0])
    writer_probe(path)
    assert snapshot(path) == before
    record_fact(
        "migration-close-refusal",
        close_attempted=True,
        product_physical_release=False,
        controller_release=True,
        result="PASS_LIMIT_NOT_HIDDEN",
    )


@pytest.mark.parametrize(
    "role,cleanup", [("backup", "close"), ("reader", "rollback"), ("reader", "close")]
)
def test_early_snapshot_release_fault_closes_main_writer(
    tmp_path, validator, monkeypatch, handles, role, cleanup
):
    path, _, before = seeded(tmp_path, validator)
    install, opened = handles
    fault = Fault()
    setattr(fault, cleanup, "ack" if cleanup == "close" else True)
    with monkeypatch.context() as patch:
        install(patch, Fault(), **{role: fault})
        with pytest.raises(RunStoreError) as raised:
            migration.migrate_v1_to_v2(
                path, tmp_path / "backup.sqlite3", validator, owners_stopped=True
            )
    assert raised.value.code == RunErrorCode.DATABASE_UNAVAILABLE
    released(path, opened)
    assert snapshot(path) == snapshot(tmp_path / "backup.sqlite3") == before
