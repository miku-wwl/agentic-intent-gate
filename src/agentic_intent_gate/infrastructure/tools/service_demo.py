"""One reviewed local demo provider. Note + retained keyed receipt commit atomically."""

import sqlite3
from uuid import uuid4

from agentic_intent_gate.application.action_compiler import ToolBinding
from agentic_intent_gate.config.configuration import resolve_database_path
from agentic_intent_gate.domain.action import ActionCode, ActionError
from agentic_intent_gate.domain.run_validation import canonical_json, valid_uuid
from agentic_intent_gate.infrastructure.persistence.sqlite_schema import (
    configure_connection,
    normalize_ddl,
    open_existing,
)
from agentic_intent_gate.infrastructure.persistence.sqlite_v2_migration import private_backup_file
from agentic_intent_gate.ports.tool_executor import Outcome, ToolOutcome

ADAPTER_ID = "demo-note-adapter"
BINDING_REVISION = "demo-binding-v1"
TABLES = {
    "resource": """CREATE TABLE resource (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1), execution_scope TEXT NOT NULL
    ) STRICT""",
    "notes": """CREATE TABLE notes (
        receipt_reference TEXT PRIMARY KEY NOT NULL, target TEXT NOT NULL, note TEXT NOT NULL
    ) STRICT""",
    "receipts": """CREATE TABLE receipts (
        effect_key TEXT PRIMARY KEY NOT NULL, payload_digest TEXT NOT NULL,
        receipt_reference TEXT NOT NULL UNIQUE REFERENCES notes(receipt_reference)
    ) STRICT""",
}


def validate_store(connection, scope):
    tables = {
        r[0]: normalize_ddl(r[1])
        for r in connection.execute("SELECT name,sql FROM sqlite_schema WHERE type='table'")
    }
    row = connection.execute("SELECT * FROM resource").fetchall()
    if (
        connection.execute("PRAGMA user_version").fetchone()[0] != 1
        or tables != {name: normalize_ddl(ddl) for name, ddl in TABLES.items()}
        or len(row) != 1
        or tuple(row[0]) != (1, scope)
        or connection.execute("PRAGMA foreign_key_check").fetchall()
        or connection.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        != connection.execute("SELECT COUNT(*) FROM receipts").fetchone()[0]
    ):
        raise ValueError("untrusted demo store")


def provision_demo_store(path, execution_scope):
    """Explicit private resource provisioning only; never invoked by an executor or recovery."""
    if not valid_uuid(execution_scope):
        raise ActionError(ActionCode.INVALID_REQUEST)
    path = resolve_database_path(path)
    private_backup_file(path)
    connection = None
    primary_error = None
    try:
        connection = sqlite3.connect(path, isolation_level=None)
        connection.execute("PRAGMA journal_mode=WAL")
        configure_connection(connection)
        connection.execute("BEGIN IMMEDIATE")
        for ddl in TABLES.values():
            connection.execute(ddl)
        connection.execute("INSERT INTO resource VALUES (1,?)", (execution_scope,))
        connection.execute("PRAGMA user_version=1")
        validate_store(connection, execution_scope)
        connection.commit()
    except ActionError as error:
        primary_error = error
        raise
    except (sqlite3.Error, OSError):
        primary_error = ActionError(ActionCode.DATABASE_UNAVAILABLE)
        raise primary_error from None
    except BaseException as error:
        primary_error = error
        raise
    finally:
        if connection is not None:
            cleanup_error = None
            try:
                try:
                    if connection.in_transaction:
                        connection.rollback()
                finally:
                    connection.close()
            except BaseException as error:
                cleanup_error = error
            if cleanup_error is not None:
                if primary_error is None:
                    primary_error = (
                        ActionError(ActionCode.DATABASE_UNAVAILABLE)
                        if isinstance(cleanup_error, Exception)
                        else cleanup_error
                    )
                    primary_error.add_note("Demo store initialization cleanup failed.")
                    raise primary_error from None
                primary_error.add_note("Demo store initialization cleanup failed.")


class DemoNoteExecutor:
    def __init__(self, path, execution_scope, *, gate_path):
        self.path = resolve_database_path(path)
        gate = resolve_database_path(gate_path)
        if (
            self.path == gate
            or (self.path.exists() and gate.exists() and self.path.samefile(gate))
            or not valid_uuid(execution_scope)
        ):
            raise ActionError(ActionCode.TOOL_NOT_BOUND)
        self.binding = ToolBinding(
            "append_service_note",
            "1",
            ADAPTER_ID,
            BINDING_REVISION,
            "service-demo",
            execution_scope,
        )

    async def execute(self, spec, effect_key, context):
        connection = None
        committed = False
        primary_error = None

        def evidence(category, *, receipt=None, reason=None):
            return ToolOutcome(
                category,
                effect_key,
                spec.execution_scope,
                spec.target,
                spec.payload_digest(),
                receipt,
                reason,
            )

        try:
            b = self.binding
            args = dict(spec.arguments)
            if (
                not valid_uuid(effect_key)
                or not valid_uuid(context.attempt_id)
                or (
                    spec.tool_name,
                    spec.tool_version,
                    spec.target,
                    spec.adapter_id,
                    spec.binding_revision,
                    spec.execution_scope,
                )
                != (
                    b.tool_name,
                    b.tool_version,
                    b.target,
                    b.adapter_id,
                    b.binding_revision,
                    b.execution_scope,
                )
                or set(args) != {"service_id", "note"}
                or args["service_id"] != "service-demo"
                or not isinstance(args["note"], str)
                or not 1 <= len(args["note"]) <= 1024
                or len(canonical_json(args).encode("utf-8")) > 4096
            ):
                return evidence(Outcome.CONFIRMED_FAILURE, reason="INPUT_REJECTED")
            if context.expired():
                return ToolOutcome(Outcome.UNKNOWN_OUTCOME)
            # mode=rw prevents missing-file creation; no reset, ATTACH, URL, subprocess or retry.
            connection = open_existing(self.path)
            configure_connection(connection)
            connection.execute("BEGIN IMMEDIATE")
            try:
                validate_store(connection, b.execution_scope)
            except ValueError:
                return evidence(Outcome.CONFIRMED_FAILURE, reason="STORE_IDENTITY_MISMATCH")
            existing = connection.execute(
                "SELECT * FROM receipts WHERE effect_key=?", (effect_key,)
            ).fetchone()
            payload = spec.payload_digest()
            if existing:
                if existing["payload_digest"] != payload:
                    return evidence(Outcome.CONFIRMED_FAILURE, reason="PAYLOAD_CONFLICT")
                note = connection.execute(
                    "SELECT * FROM notes WHERE receipt_reference=?",
                    (existing["receipt_reference"],),
                ).fetchone()
                if note is None or (note["target"], note["note"]) != (spec.target, args["note"]):
                    return ToolOutcome(Outcome.UNKNOWN_OUTCOME)
                return evidence(Outcome.SUCCESS, receipt=existing["receipt_reference"])
            if context.expired():
                return ToolOutcome(Outcome.UNKNOWN_OUTCOME)
            receipt = str(uuid4())
            connection.execute(
                "INSERT INTO notes VALUES (?,?,?)", (receipt, spec.target, args["note"])
            )
            connection.execute(
                "INSERT INTO receipts VALUES (?,?,?)", (effect_key, payload, receipt)
            )
            if context.expired():
                return ToolOutcome(Outcome.UNKNOWN_OUTCOME)
            connection.commit()
            committed = True
            return evidence(Outcome.SUCCESS, receipt=receipt)
        except Exception as error:
            # Including ambiguous downstream COMMIT. A transport/storage fault never proves failure.
            primary_error = error
            return ToolOutcome(Outcome.UNKNOWN_OUTCOME)
        except BaseException as error:
            primary_error = error
            raise
        finally:
            if connection is not None:
                cleanup_error = None
                try:
                    try:
                        if connection.in_transaction and not committed:
                            connection.rollback()
                    finally:
                        connection.close()
                except BaseException as error:
                    cleanup_error = error
                if cleanup_error is not None:
                    if primary_error is not None:
                        primary_error.add_note(
                            "Demo execution cleanup failed; outcome remains unknown."
                        )
                    elif isinstance(cleanup_error, Exception):
                        return ToolOutcome(Outcome.UNKNOWN_OUTCOME)
                    else:
                        raise cleanup_error
