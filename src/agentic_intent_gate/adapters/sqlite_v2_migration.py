"""Offline upgrade. The operator must stop every owner; a write lock is not proof of that."""

import argparse
import csv
import os
import sqlite3
import subprocess
from pathlib import Path

from agentic_intent_gate.adapters.configuration import resolve_database_path
from agentic_intent_gate.adapters.sqlite_run_repository import SQLiteRunRepository
from agentic_intent_gate.adapters.sqlite_schema import (
    configure_connection,
    open_existing,
)
from agentic_intent_gate.adapters.sqlite_schema import (
    validate_schema as validate_v1,
)
from agentic_intent_gate.adapters.sqlite_v2_runs import SQLiteV2RunRepository
from agentic_intent_gate.adapters.sqlite_v2_schema import ADDITIONS, validate_schema
from agentic_intent_gate.adapters.tool_catalog import build_validator
from agentic_intent_gate.domain.run import RunErrorCode, RunStoreError
from agentic_intent_gate.domain.validation import fail


def private_backup_file(path):
    # Never overwrite, follow an existing link, or write business data before privacy succeeds.
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    if os.name == "nt":
        result = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True, check=True
        )
        sid = next(csv.reader(result.stdout.splitlines()))[1]
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"*{sid}:F"],
            capture_output=True,
            check=True,
        )


def validate_rows(connection, repository):
    if [r[0] for r in connection.execute("PRAGMA integrity_check")] != ["ok"]:
        fail("STORED_INTEGRITY")
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        fail("STORED_FOREIGN_KEYS")
    for row in connection.execute("SELECT run_id FROM runs").fetchall():
        repository._load(connection, row[0])


def migrate_v1_to_v2(path, backup_path, validator, *, owners_stopped=False):
    if owners_stopped is not True:
        raise RunStoreError(RunErrorCode.DATABASE_UNAVAILABLE)
    path, backup_path = resolve_database_path(path), resolve_database_path(backup_path)
    if path == backup_path or backup_path.exists() or not path.is_file():
        raise RunStoreError(RunErrorCode.DATABASE_UNAVAILABLE)
    connection = reader = backup = None
    primary_error = None
    try:
        connection = open_existing(path)
        configure_connection(connection)
        connection.execute("BEGIN IMMEDIATE")
        validate_v1(connection)
        old = SQLiteRunRepository.__new__(SQLiteRunRepository)
        old.validator = validator
        validate_rows(connection, old)
        private_backup_file(backup_path)
        # Separate read snapshot includes committed WAL pages while the writer lock fences writes.
        reader = open_existing(path)
        configure_connection(reader)
        reader.execute("BEGIN")
        backup = sqlite3.connect(backup_path, isolation_level=None)
        reader.backup(backup)
        backup.row_factory = sqlite3.Row
        validate_v1(backup)
        validate_rows(backup, old)
        backup.close()
        backup = None
        reader.rollback()
        reader.close()
        reader = None
        for ddl in ADDITIONS.values():
            connection.execute(ddl)
        connection.execute(
            "INSERT INTO approval_plan_bindings(decision_id, binding_status) "
            "SELECT decision_id, 'LEGACY_UNBOUND' FROM approval_decisions"
        )
        validate_schema(connection, migrating=True)
        new = SQLiteV2RunRepository.__new__(SQLiteV2RunRepository)
        new.validator = validator
        validate_rows(connection, new)
        connection.execute("PRAGMA user_version = 2")
        validate_schema(connection)
        connection.commit()
        return backup_path
    except RunStoreError as error:
        primary_error = error
        raise
    except Exception:
        primary_error = RunStoreError(RunErrorCode.DATABASE_UNAVAILABLE)
        raise primary_error from None
    except BaseException as error:
        primary_error = error
        raise
    finally:
        cleanup_error = None
        for item in (backup, reader, connection):
            if item is not None:
                try:
                    try:
                        if item.in_transaction:
                            item.rollback()
                    finally:
                        item.close()
                except BaseException as error:
                    # One faulty handle must not skip close on any other acquired handle.
                    if cleanup_error is None:
                        cleanup_error = error
        if cleanup_error is not None:
            if primary_error is None:
                primary_error = (
                    RunStoreError(RunErrorCode.DATABASE_UNAVAILABLE)
                    if isinstance(cleanup_error, Exception)
                    else cleanup_error
                )
                primary_error.add_note("Migration cleanup failed; inspect offline before retry.")
                raise primary_error from None
            # Preserve primary identity/category, without exposing raw SQL, paths or faults.
            primary_error.add_note("Migration cleanup failed; inspect offline before retry.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument(
        "backup", type=Path, help="New private backup file in an existing directory"
    )
    parser.add_argument(
        "--owners-stopped",
        action="store_true",
        required=True,
        help="Operator attestation: all app/helper owners have been stopped",
    )
    args = parser.parse_args()
    try:
        migrate_v1_to_v2(
            args.database, args.backup, build_validator(), owners_stopped=args.owners_stopped
        )
    except RunStoreError:
        parser.exit(2, "Migration failed closed. Inspect the database offline; do not downgrade.\n")
    print("SQLITE_V2_MIGRATION=PASS")


if __name__ == "__main__":
    main()
