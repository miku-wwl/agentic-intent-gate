"""Transaction cleanup consolidation: real temporary SQLite files and retained handles."""

import asyncio
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass, replace
from functools import partial
from uuid import uuid4

import pytest
from execution_process_helpers import record_fact
from run_helpers import advance
from test_service_demo_adapter import downstream, invoke

import agentic_intent_gate.infrastructure.persistence.sqlite_run_repository as sqlite_run_repository
import agentic_intent_gate.infrastructure.persistence.sqlite_schema as sqlite_schema
import agentic_intent_gate.infrastructure.persistence.sqlite_v2_schema as sqlite_v2_schema
import agentic_intent_gate.infrastructure.tools.service_demo as service_demo
from agentic_intent_gate.domain.action import ActionCode, ActionError
from agentic_intent_gate.domain.approval import ApprovalChoice, ApprovalDecisionRequest
from agentic_intent_gate.domain.contracts import Risk
from agentic_intent_gate.domain.run import RunErrorCode, RunState, RunStoreError
from agentic_intent_gate.infrastructure.persistence.sqlite_run_repository import SQLiteRunRepository
from agentic_intent_gate.infrastructure.tools.service_demo import provision_demo_store
from agentic_intent_gate.ports.tool_executor import ExecutionSpec, Outcome


@dataclass
class Injection:
    statement: str = ""
    after_statement: bool = False
    rollback_error: bool = False
    inspect_error: bool = False
    close_mode: str = ""
    commit_mode: str = ""


class RetainedConnections:
    """Connection factory preserves real SQLite handles until assertions complete."""

    def __init__(self, connect, injection):
        self.connect, self.injection, self.connections = connect, injection, []

    def factory(self, *args, **kwargs):
        injection = self.injection

        class FaultConnection(sqlite3.Connection):
            armed = False
            rollback_attempts = 0
            close_attempts = 0
            transaction_before_close = None

            def execute(self, statement, parameters=()):
                matching = injection.statement and statement.startswith(injection.statement)
                if matching and not injection.after_statement:
                    self.armed = True
                    raise sqlite3.OperationalError("controlled SQLite operation fault")
                cursor = super().execute(statement, parameters)
                if statement.startswith("BEGIN"):
                    self.armed = True
                if matching:
                    self.armed = True
                    raise sqlite3.OperationalError("controlled post-write fault")
                return cursor

            def commit(self):
                if injection.commit_mode == "before":
                    raise sqlite3.OperationalError("controlled pre-commit fault")
                super().commit()
                if injection.commit_mode == "cancel-after":
                    raise asyncio.CancelledError
                if injection.commit_mode == "after":
                    raise sqlite3.OperationalError("controlled lost commit acknowledgement")

            @property
            def in_transaction(self):
                if self.armed and injection.inspect_error:
                    raise sqlite3.OperationalError("controlled transaction inspection fault")
                return super().in_transaction

            def rollback(self):
                self.rollback_attempts += 1
                if self.armed and injection.rollback_error:
                    raise sqlite3.OperationalError("controlled rollback fault")
                return super().rollback()

            def close(self):
                self.close_attempts += 1
                try:
                    self.transaction_before_close = sqlite3.Connection.in_transaction.__get__(self)
                except sqlite3.ProgrammingError:
                    self.transaction_before_close = "already closed"
                if injection.close_mode != "refuse":
                    super().close()
                if injection.close_mode:
                    raise sqlite3.OperationalError("controlled close acknowledgement fault")

        connection = self.connect(*args, factory=FaultConnection, **kwargs)
        self.connections.append(connection)
        return connection

    def patch_module(self, monkeypatch, module, attribute="sqlite3"):
        sqlite_module = getattr(module, attribute)

        def connect(path, *args, **kwargs):
            # The v2 schema's in-memory DDL reference is not an owned business connection.
            if str(path) == ":memory:":
                return self.connect(path, *args, **kwargs)
            return self.factory(path, *args, **kwargs)

        monkeypatch.setattr(sqlite_module, "connect", connect)

    def patch_opener(self, monkeypatch, module, attribute="open_existing"):
        def open_fault(path):
            return self.factory(path, isolation_level=None)

        monkeypatch.setattr(module, attribute, open_fault)

    def controller_cleanup(self):
        for connection in self.connections:
            try:
                sqlite3.Connection.rollback(connection)
            except sqlite3.Error:
                pass
            try:
                sqlite3.Connection.close(connection)
            except sqlite3.Error:
                pass


def schema_snapshot(path):
    with closing(sqlite3.connect(path, timeout=0.05)) as connection:
        return {
            "version": connection.execute("PRAGMA user_version").fetchone()[0],
            "tables": connection.execute(
                "SELECT name, sql FROM sqlite_schema WHERE type='table' ORDER BY name"
            ).fetchall(),
            "integrity": connection.execute("PRAGMA integrity_check").fetchall(),
            "foreign_keys": connection.execute("PRAGMA foreign_key_check").fetchall(),
        }


def writer_commit(path, *, demo=False):
    started = time.monotonic()
    try:
        with closing(sqlite3.connect(path, isolation_level=None, timeout=0.05)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if demo:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_schema WHERE type='table'"
                    )
                }
                resource = "resource" in tables
                if resource:
                    connection.execute("UPDATE resource SET execution_scope=execution_scope")
                elif "runs" in tables:
                    connection.execute("UPDATE runs SET intent=intent")
                else:
                    connection.execute(
                        "CREATE TABLE consolidation_writer_probe(value INTEGER) STRICT"
                    )
                    connection.execute("INSERT INTO consolidation_writer_probe VALUES (1)")
                    connection.execute("DROP TABLE consolidation_writer_probe")
            else:
                connection.execute("CREATE TABLE consolidation_writer_probe(value INTEGER) STRICT")
                connection.execute("INSERT INTO consolidation_writer_probe VALUES (1)")
                connection.execute("DROP TABLE consolidation_writer_probe")
            connection.commit()
        return {"result": "PASS", "seconds": time.monotonic() - started}
    except sqlite3.OperationalError:
        return {"result": "BLOCKED", "seconds": time.monotonic() - started}


def assert_safe_storage_error(error, expected):
    assert isinstance(error, expected)
    assert getattr(error, "code", None) in {
        RunErrorCode.DATABASE_UNAVAILABLE,
        ActionCode.DATABASE_UNAVAILABLE,
    }
    assert "controlled" not in str(error)


def run_demo_executor(env):
    return asyncio.run(invoke(env))


def _transaction_before_close(connection):
    if connection.transaction_before_close is not None:
        return connection.transaction_before_close
    return sqlite3.Connection.in_transaction.__get__(connection)


@pytest.mark.parametrize("target", ["v1-schema", "run-repository", "executor"])
def test_suspicious_path_cleanup_contract(tmp_path, validator, monkeypatch, demo_env, target):
    """Retest each independently reproduced cross-audit candidate after correction."""
    if target == "v1-schema":
        path = tmp_path / "v1.sqlite3"
        fault = Injection("CREATE TABLE approval_decisions", rollback_error=True)
        module = sqlite_schema
        operation = partial(sqlite_schema.initialize_database, path)
    elif target == "run-repository":
        path = tmp_path / "runs.sqlite3"
        repository = SQLiteRunRepository(path, validator)
        waiting = advance(repository, RunState.AwaitingApproval, Risk.SIDE_EFFECT)
        fault = Injection(rollback_error=True)
        module = sqlite_run_repository
        operation = partial(
            repository.decide_approval,
            waiting.run_id,
            ApprovalDecisionRequest(ApprovalChoice.APPROVE, waiting.version - 1),
        )
    else:
        path = demo_env.demo_path
        fault = Injection("INSERT INTO receipts", rollback_error=True)
        module = service_demo
        operation = partial(run_demo_executor, demo_env)

    retained = RetainedConnections(sqlite3.connect, fault)
    captured = None
    with monkeypatch.context() as patch:
        if target == "v1-schema":
            retained.patch_module(patch, module)
        else:
            retained.patch_opener(patch, module)
        try:
            captured = operation()
        except BaseException as error:
            captured = error

    connection = retained.connections[0]
    transaction = _transaction_before_close(connection)
    before = schema_snapshot(path)
    before_writer = writer_commit(path, demo=target != "v1-schema")
    retained.controller_cleanup()
    after_writer = writer_commit(path, demo=target != "v1-schema")
    after = schema_snapshot(path)
    outcome = getattr(captured, "category", None)
    observation = {
        "target": target,
        "exception_or_outcome": type(captured).__name__,
        "public_error_code": getattr(captured, "code", None),
        "outcome": getattr(outcome, "value", outcome),
        "rollback_attempts": connection.rollback_attempts,
        "close_attempts": connection.close_attempts,
        "transaction_before_close": transaction,
        "writer_before_cleanup": before_writer,
        "writer_after_controller_cleanup": after_writer,
        "schema_before_cleanup": before,
        "schema_after_cleanup": after,
        "strong_reference_retained": True,
    }
    record_fact("postfix-suspicious-path", **observation)

    assert transaction
    assert after_writer["result"] == "PASS"
    if target == "v1-schema":
        assert isinstance(captured, RunStoreError)
        assert captured.code == RunErrorCode.DATABASE_UNAVAILABLE
        assert connection.rollback_attempts == connection.close_attempts == 1
        assert before_writer["result"] == "PASS"
        assert before["tables"] == []
        assert after["tables"] == []
    elif target == "run-repository":
        assert isinstance(captured, RunStoreError)
        assert captured.code == RunErrorCode.STALE_RUN_VERSION
        assert connection.rollback_attempts == connection.close_attempts == 1
        assert before_writer["result"] == "PASS"
        assert repository.get(waiting.run_id).state is RunState.AwaitingApproval
    else:
        assert captured.category is Outcome.UNKNOWN_OUTCOME
        assert connection.rollback_attempts == connection.close_attempts == 1
        assert before_writer["result"] == "PASS"
        assert before["tables"] == after["tables"]
        assert downstream(path)["notes"] == downstream(path)["receipts"] == []


@pytest.mark.parametrize("repetition", [1, 2])
def test_v2_schema_rollback_failure_closes_and_preserves_atomicity(
    tmp_path, validator, monkeypatch, repetition
):
    sqlite_v2_schema.expected_objects()  # Prime only the non-business in-memory DDL cache.
    path = tmp_path / "fresh-v2.sqlite3"
    fault = Injection("CREATE TABLE actions", rollback_error=True)
    retained = RetainedConnections(sqlite3.connect, fault)
    captured = None
    with monkeypatch.context() as patch:
        retained.patch_module(patch, sqlite_v2_schema)
        try:
            sqlite_v2_schema.initialize_database(path)
        except BaseException as error:
            captured = error
    connection = retained.connections[0]
    transaction = _transaction_before_close(connection)
    before_writer = writer_commit(path)
    before_cleanup = schema_snapshot(path)
    retained.controller_cleanup()
    after_cleanup = schema_snapshot(path)
    after_writer = writer_commit(path)
    observation = {
        "target": "sqlite_v2_schema.initialize_database",
        "repetition": repetition,
        "exception": type(captured).__name__ if captured else None,
        "safe_category": getattr(captured, "code", None),
        "rollback_attempts": connection.rollback_attempts,
        "close_attempts": connection.close_attempts,
        "transaction_before_cleanup": transaction,
        "writer_before_cleanup": before_writer,
        "writer_after_controller_cleanup": after_writer,
        "schema_before_diagnostic_writer": before_cleanup,
        "schema_after_controller_cleanup": after_cleanup,
        "elapsed_seconds": before_writer["seconds"],
        "strong_reference_retained": True,
    }
    record_fact("cleanup-reproduction", **observation)
    assert isinstance(captured, RunStoreError)
    assert captured.code == RunErrorCode.DATABASE_UNAVAILABLE
    assert connection.rollback_attempts == connection.close_attempts == 1
    assert transaction and before_writer["result"] == "PASS"
    assert after_writer["result"] == "PASS"
    assert before_cleanup["tables"] == after_cleanup["tables"] == []
    assert after_cleanup["version"] == 0 and after_cleanup["integrity"] == [("ok",)]


@pytest.mark.parametrize("repetition", [1, 2])
def test_demo_provision_rollback_failure_closes_and_preserves_atomicity(
    tmp_path, monkeypatch, repetition
):
    path, scope = tmp_path / "fresh-demo.sqlite3", str(uuid4())
    fault = Injection("INSERT INTO resource", after_statement=True, rollback_error=True)
    retained = RetainedConnections(sqlite3.connect, fault)
    captured = None
    with monkeypatch.context() as patch:
        retained.patch_module(patch, service_demo)
        try:
            provision_demo_store(path, scope)
        except BaseException as error:
            captured = error
    connection = retained.connections[0]
    transaction = _transaction_before_close(connection)
    before_writer = writer_commit(path, demo=True)
    retained.controller_cleanup()
    after_cleanup = schema_snapshot(path)
    after_writer = writer_commit(path, demo=True)
    observation = {
        "target": "service_demo.provision_demo_store",
        "repetition": repetition,
        "exception": type(captured).__name__ if captured else None,
        "rollback_attempts": connection.rollback_attempts,
        "close_attempts": connection.close_attempts,
        "transaction_before_cleanup": transaction,
        "writer_before_cleanup": before_writer,
        "writer_after_controller_cleanup": after_writer,
        "schema_after_controller_cleanup": after_cleanup,
        "elapsed_seconds": before_writer["seconds"],
        "strong_reference_retained": True,
    }
    record_fact("cleanup-reproduction", **observation)
    assert isinstance(captured, ActionError)
    assert captured.code == ActionCode.DATABASE_UNAVAILABLE
    assert connection.rollback_attempts == connection.close_attempts == 1
    assert transaction and before_writer["result"] == "PASS"
    assert after_writer["result"] == "PASS"
    assert after_cleanup["tables"] == [] and after_cleanup["integrity"] == [("ok",)]


@pytest.mark.parametrize(
    "target",
    ["v2", "provision", "v1", "run-repository", "executor"],
)
def test_targeted_cleanup_matrix_and_safe_error(tmp_path, validator, monkeypatch, demo_env, target):
    """Exercise DDL/write failures plus cleanup precedence on each real connection owner."""
    if target == "v2":
        sqlite_v2_schema.expected_objects()
        path, fault, module = (
            tmp_path / "v2.sqlite3",
            Injection("CREATE TABLE actions", rollback_error=True),
            sqlite_v2_schema,
        )
        operation = partial(sqlite_v2_schema.initialize_database, path)
        expected, demo = RunStoreError, False
    elif target == "provision":
        path, fault, module = (
            tmp_path / "demo.sqlite3",
            Injection("INSERT INTO resource", after_statement=True, rollback_error=True),
            service_demo,
        )
        operation = partial(provision_demo_store, path, str(uuid4()))
        expected, demo = ActionError, True
    elif target == "v1":
        path, fault, module = (
            tmp_path / "v1.sqlite3",
            Injection("CREATE TABLE approval_decisions", rollback_error=True),
            sqlite_schema,
        )
        operation = partial(sqlite_schema.initialize_database, path)
        expected, demo = RunStoreError, False
    elif target == "run-repository":
        path = tmp_path / "run-repository.sqlite3"
        repository = SQLiteRunRepository(path, validator)
        waiting = advance(repository, RunState.AwaitingApproval, Risk.SIDE_EFFECT)
        fault, module = Injection(rollback_error=True), sqlite_run_repository
        operation = partial(
            repository.decide_approval,
            waiting.run_id,
            ApprovalDecisionRequest(ApprovalChoice.APPROVE, waiting.version - 1),
        )
        expected, demo = RunStoreError, True
    else:
        path, fault, module = (
            demo_env.demo_path,
            Injection("INSERT INTO receipts", rollback_error=True),
            service_demo,
        )
        operation = partial(run_demo_executor, demo_env)
        expected, demo = None, True

    retained = RetainedConnections(sqlite3.connect, fault)
    captured = None
    with monkeypatch.context() as patch:
        if target in {"v2", "provision", "v1"}:
            if target == "v2":
                retained.patch_module(patch, module)
            elif target == "v1":
                retained.patch_module(patch, module)
            else:
                retained.patch_module(patch, module)
        else:
            retained.patch_opener(patch, module)
        try:
            captured = operation()
        except BaseException as error:
            captured = error
    connection = retained.connections[0]
    transaction = _transaction_before_close(connection)
    before = schema_snapshot(path)
    before_writer = writer_commit(path, demo=demo)
    retained.controller_cleanup()
    after_cleanup = schema_snapshot(path)
    after_writer = writer_commit(path, demo=demo)
    observation = {
        "target": target,
        "exception_or_outcome": type(captured).__name__,
        "rollback_attempts": connection.rollback_attempts,
        "close_attempts": connection.close_attempts,
        "transaction_before_cleanup": transaction,
        "writer_before_cleanup": before_writer,
        "writer_after_controller_cleanup": after_writer,
        "schema_after_controller_cleanup": after_cleanup,
    }
    record_fact("cleanup-matrix", **observation)
    assert connection.close_attempts == 1
    assert transaction
    assert before_writer["result"] == "PASS"
    assert after_writer["result"] == "PASS"
    if target == "executor":
        assert captured.category is Outcome.UNKNOWN_OUTCOME
        assert downstream(path)["notes"] == downstream(path)["receipts"] == []
    else:
        if target == "run-repository":
            assert isinstance(captured, RunStoreError)
            assert captured.code == RunErrorCode.STALE_RUN_VERSION
        else:
            assert_safe_storage_error(captured, expected)
    if target == "v2":
        assert before["tables"] == after_cleanup["tables"] == []
        assert after_cleanup["version"] == 0 and after_cleanup["integrity"] == [("ok",)]
    elif target == "provision":
        assert after_cleanup["tables"] == []
    elif target == "v1":
        assert before["tables"] == []
        assert after_cleanup["tables"] == [] and after_cleanup["version"] == 0
    elif target == "run-repository":
        assert captured.code == RunErrorCode.STALE_RUN_VERSION
        assert repository.get(waiting.run_id).state is RunState.AwaitingApproval


@pytest.mark.parametrize(
    ("fault", "expected_rollbacks", "transaction_before_close"),
    [
        (Injection("CREATE TABLE actions"), 1, False),
        (Injection("CREATE TABLE actions", inspect_error=True), 0, True),
        (Injection("CREATE TABLE actions", rollback_error=True, close_mode="ack"), 1, True),
    ],
)
def test_v2_cleanup_exception_matrix(
    tmp_path, monkeypatch, fault, expected_rollbacks, transaction_before_close
):
    path = tmp_path / "v2-cleanup.sqlite3"
    retained = RetainedConnections(sqlite3.connect, fault)
    with monkeypatch.context() as patch:
        retained.patch_module(patch, sqlite_v2_schema)
        with pytest.raises(RunStoreError) as raised:
            sqlite_v2_schema.initialize_database(path)
    connection = retained.connections[0]
    assert raised.value.code == RunErrorCode.DATABASE_UNAVAILABLE
    assert connection.rollback_attempts == expected_rollbacks
    assert connection.close_attempts == 1
    assert connection.transaction_before_close is transaction_before_close
    assert schema_snapshot(path)["tables"] == []
    writer = writer_commit(path)
    record_fact(
        "v2-cleanup-secondary-fault",
        rollback_attempts=connection.rollback_attempts,
        close_attempts=connection.close_attempts,
        close_mode=fault.close_mode,
        writer=writer,
        schema=schema_snapshot(path),
    )
    assert writer["result"] == "PASS"
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")


def test_physical_close_refusal_is_not_reported_as_lock_release(tmp_path, monkeypatch):
    path = tmp_path / "close-refused.sqlite3"
    fault = Injection("CREATE TABLE actions", rollback_error=True, close_mode="refuse")
    retained = RetainedConnections(sqlite3.connect, fault)
    with monkeypatch.context() as patch:
        retained.patch_module(patch, sqlite_v2_schema)
        with pytest.raises(RunStoreError) as raised:
            sqlite_v2_schema.initialize_database(path)
    connection = retained.connections[0]
    transaction = sqlite3.Connection.in_transaction.__get__(connection)
    blocked_writer = writer_commit(path)
    record_fact(
        "physical-close-refusal",
        error_code=raised.value.code.value,
        rollback_attempts=connection.rollback_attempts,
        close_attempts=connection.close_attempts,
        transaction_retained=transaction,
        writer=blocked_writer,
        physical_close_claimed=False,
    )
    assert connection.close_attempts == 1 and transaction
    assert blocked_writer["result"] == "BLOCKED"
    retained.controller_cleanup()
    assert writer_commit(path)["result"] == "PASS"
    assert schema_snapshot(path)["tables"] == []


def test_provision_failure_before_begin_still_closes(tmp_path, monkeypatch):
    path = tmp_path / "provision-before-begin.sqlite3"
    retained = RetainedConnections(
        sqlite3.connect, Injection("BEGIN IMMEDIATE", rollback_error=True)
    )
    with monkeypatch.context() as patch:
        retained.patch_module(patch, service_demo)
        with pytest.raises(ActionError) as raised:
            provision_demo_store(path, str(uuid4()))
    connection = retained.connections[0]
    assert raised.value.code == ActionCode.DATABASE_UNAVAILABLE
    assert connection.rollback_attempts == 0 and connection.close_attempts == 1
    assert connection.transaction_before_close is False
    assert schema_snapshot(path)["tables"] == []
    assert writer_commit(path, demo=True)["result"] == "PASS"


def test_v2_initialization_idempotence_and_reopen_after_rollback(tmp_path, monkeypatch):
    path = tmp_path / "v2-reopen.sqlite3"
    retained = RetainedConnections(
        sqlite3.connect, Injection("CREATE TABLE actions", rollback_error=True)
    )
    with monkeypatch.context() as patch:
        retained.patch_module(patch, sqlite_v2_schema)
        with pytest.raises(RunStoreError):
            sqlite_v2_schema.initialize_database(path)
    assert retained.connections[0].close_attempts == 1
    failed_snapshot = schema_snapshot(path)
    sqlite_v2_schema.initialize_database(path)
    first = schema_snapshot(path)
    sqlite_v2_schema.initialize_database(path)
    second = schema_snapshot(path)
    with closing(sqlite3.connect(path)) as connection:
        strict_tables = {row[1] for row in connection.execute("PRAGMA table_list") if row[5] == 1}
        indexes = connection.execute("SELECT name FROM sqlite_schema WHERE type='index'").fetchall()
    assert failed_snapshot["tables"] == [] and failed_snapshot["version"] == 0
    assert first == second and first["version"] == 2
    assert len(first["tables"]) == 9 and strict_tables == {name for name, _ in first["tables"]}
    assert indexes
    record_fact(
        "v2-reopen-idempotence",
        failed_state=failed_snapshot,
        initialized_state=first,
        strict_table_count=len(strict_tables),
        index_count=len(indexes),
        reopen="PASS",
        idempotent="PASS",
    )


def test_v2_expected_objects_memory_connection_is_closed(monkeypatch):
    sqlite_v2_schema.expected_objects.cache_clear()
    retained = RetainedConnections(sqlite3.connect, Injection())
    with monkeypatch.context() as patch:
        patch.setattr(sqlite_v2_schema.sqlite3, "connect", retained.factory)
        sqlite_v2_schema.expected_objects()
    connection = retained.connections[0]
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")
    record_fact(
        "prepatch-memory-reference-connection",
        close_attempts=connection.close_attempts,
        closed_after_owner_returned=connection.close_attempts == 1,
        database="in-memory-only",
    )
    assert connection.close_attempts == 1
    sqlite_v2_schema.expected_objects.cache_clear()


def test_repeated_demo_provisioning_refuses_without_overwriting_data(tmp_path):
    path, scope = tmp_path / "repeat-demo.sqlite3", str(uuid4())
    provision_demo_store(path, scope)
    note_id, effect_key = str(uuid4()), str(uuid4())
    with closing(sqlite3.connect(path, isolation_level=None)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("INSERT INTO notes VALUES (?,?,?)", (note_id, "service-demo", "keep"))
        connection.execute("INSERT INTO receipts VALUES (?,?,?)", (effect_key, "digest", note_id))
        connection.commit()
    before = downstream(path)
    with pytest.raises(FileExistsError):
        provision_demo_store(path, scope)
    after = downstream(path)
    assert before == after
    record_fact("repeat-demo-provision", result="SAFE_NON_OVERWRITE_REFUSAL", preserved=True)


@pytest.mark.parametrize(
    ("fault", "persisted", "outcome"),
    [
        (Injection(commit_mode="before"), False, Outcome.UNKNOWN_OUTCOME),
        (Injection(commit_mode="after"), True, Outcome.UNKNOWN_OUTCOME),
        (Injection(close_mode="ack"), True, Outcome.UNKNOWN_OUTCOME),
    ],
)
def test_executor_commit_and_close_uncertainty(demo_env, monkeypatch, fault, persisted, outcome):
    retained = RetainedConnections(sqlite3.connect, fault)
    with monkeypatch.context() as patch:
        retained.patch_opener(patch, service_demo)
        result = asyncio.run(invoke(demo_env))
    connection = retained.connections[0]
    rows = downstream(demo_env.demo_path)
    assert result.category is outcome
    assert connection.close_attempts == 1
    assert len(rows["notes"]) == len(rows["receipts"]) == int(persisted)
    if fault.commit_mode == "before":
        assert connection.rollback_attempts == 1
        assert connection.transaction_before_close is False
    else:
        assert connection.rollback_attempts == 0
        assert connection.transaction_before_close is False
    assert writer_commit(demo_env.demo_path, demo=True)["result"] == "PASS"
    record_fact(
        "executor-uncertainty",
        commit_mode=fault.commit_mode,
        close_mode=fault.close_mode,
        outcome=result.category.value,
        notes=len(rows["notes"]),
        receipts=len(rows["receipts"]),
        rollback_attempts=connection.rollback_attempts,
        close_attempts=connection.close_attempts,
        retry_count=0,
    )
    if persisted:
        receipt = rows["receipts"][0][2]
        duplicate = asyncio.run(invoke(demo_env))
        assert duplicate.category is Outcome.SUCCESS
        assert duplicate.receipt_reference == receipt
        assert downstream(demo_env.demo_path) == rows


def test_executor_cancellation_after_commit_is_not_retried(demo_env, monkeypatch):
    retained = RetainedConnections(sqlite3.connect, Injection(commit_mode="cancel-after"))
    with monkeypatch.context() as patch:
        retained.patch_opener(patch, service_demo)
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(invoke(demo_env))
    assert len(retained.connections) == 1
    assert retained.connections[0].close_attempts == 1
    rows = downstream(demo_env.demo_path)
    assert len(rows["notes"]) == len(rows["receipts"]) == 1
    replay = asyncio.run(invoke(demo_env))
    assert replay.category is Outcome.SUCCESS
    assert replay.receipt_reference == rows["receipts"][0][2]
    assert downstream(demo_env.demo_path) == rows
    record_fact(
        "executor-cancellation-after-commit",
        propagated=True,
        persisted_notes=1,
        persisted_receipts=1,
        first_attempt_connections=len(retained.connections),
        explicit_replay="SAME_RECEIPT_NO_DUPLICATE",
    )


def test_executor_connection_unavailable_and_payload_conflict(demo_env, monkeypatch):
    original = downstream(demo_env.demo_path)
    monkeypatch.setattr(
        service_demo,
        "open_existing",
        lambda path: (_ for _ in ()).throw(sqlite3.OperationalError("controlled unavailable")),
    )
    unavailable = asyncio.run(invoke(demo_env))
    assert unavailable.category is Outcome.UNKNOWN_OUTCOME
    monkeypatch.undo()
    first = asyncio.run(invoke(demo_env))
    assert first.category is Outcome.SUCCESS
    changed = replace(
        ExecutionSpec.from_compiled(demo_env.action.spec),
        arguments={"service_id": "service-demo", "note": "different payload"},
    )
    conflict = asyncio.run(invoke(demo_env, changed))
    assert conflict.category is Outcome.CONFIRMED_FAILURE
    assert conflict.no_effect_reason == "PAYLOAD_CONFLICT"
    rows = downstream(demo_env.demo_path)
    assert len(rows["notes"]) == len(rows["receipts"]) == 1
    assert rows != original
    record_fact(
        "executor-unavailable-conflict",
        unavailable=unavailable.category.value,
        success=first.category.value,
        conflict=conflict.no_effect_reason,
        durable_notes=len(rows["notes"]),
        durable_receipts=len(rows["receipts"]),
    )
