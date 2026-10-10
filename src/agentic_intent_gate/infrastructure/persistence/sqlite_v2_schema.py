"""Explicit nine-table contract; never an automatic v1 upgrade."""

import sqlite3
from contextlib import closing
from functools import lru_cache
from pathlib import Path

from agentic_intent_gate.domain.run import RunErrorCode, RunStoreError
from agentic_intent_gate.infrastructure.persistence.sqlite_schema import (
    BUSY_TIMEOUT_MS,
    configure_connection,
    normalize_ddl,
    open_existing,
)
from agentic_intent_gate.infrastructure.persistence.sqlite_schema import (
    TABLES as V1_TABLES,
)

HEX = "length({0}) = 64 AND {0} NOT GLOB '*[^0-9a-f]*'"
DIGEST = HEX.format("approval_plan_digest")
ADDITIONS = {
    "approval_plan_bindings": f"""CREATE TABLE approval_plan_bindings (
    decision_id TEXT PRIMARY KEY NOT NULL REFERENCES approval_decisions(decision_id),
    binding_status TEXT NOT NULL CHECK(binding_status IN ('BOUND_V1', 'LEGACY_UNBOUND')),
    identity_revision TEXT,
    approval_plan_digest TEXT,
    bound_at TEXT,
    CHECK((binding_status = 'LEGACY_UNBOUND' AND identity_revision IS NULL
        AND approval_plan_digest IS NULL AND bound_at IS NULL)
        OR (binding_status = 'BOUND_V1' AND identity_revision IS 'approval-plan-v1'
        AND approval_plan_digest IS NOT NULL AND {DIGEST}
        AND bound_at IS NOT NULL AND length(bound_at) = 27))
) STRICT""",
    "actions": f"""CREATE TABLE actions (
    action_id TEXT PRIMARY KEY NOT NULL CHECK(length(action_id) = 36),
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    approved_run_version INTEGER NOT NULL CHECK(approved_run_version = 5),
    approval_decision_id TEXT NOT NULL REFERENCES approval_plan_bindings(decision_id),
    plan_digest TEXT NOT NULL CHECK({HEX.format("plan_digest")}),
    step_index INTEGER NOT NULL CHECK(step_index BETWEEN 0 AND 4),
    tool_name TEXT NOT NULL CHECK(length(tool_name) BETWEEN 1 AND 64),
    tool_version TEXT NOT NULL CHECK(length(tool_version) BETWEEN 1 AND 64),
    target TEXT NOT NULL CHECK(length(target) BETWEEN 1 AND 64),
    normalized_arguments_json TEXT NOT NULL CHECK(CASE WHEN json_valid(normalized_arguments_json)
        THEN json_type(normalized_arguments_json) = 'object'
        AND length(CAST(normalized_arguments_json AS BLOB)) <= 4096 ELSE 0 END),
    risk_class TEXT NOT NULL CHECK(risk_class = 'SIDE_EFFECT'),
    compiler_revision TEXT NOT NULL CHECK(length(compiler_revision) BETWEEN 1 AND 64),
    adapter_id TEXT NOT NULL CHECK(length(adapter_id) BETWEEN 1 AND 64),
    binding_revision TEXT NOT NULL CHECK(length(binding_revision) BETWEEN 1 AND 64),
    execution_scope TEXT NOT NULL CHECK(length(execution_scope) = 36),
    spec_digest TEXT NOT NULL CHECK({HEX.format("spec_digest")}),
    effect_key TEXT NOT NULL CHECK(length(effect_key) = 36),
    created_at TEXT NOT NULL CHECK(length(created_at) = 27),
    state TEXT NOT NULL CHECK(state IN
        ('Pending', 'Authorized', 'Executing', 'Succeeded', 'Failed', 'Unknown')),
    version INTEGER NOT NULL CHECK(version BETWEEN 1 AND 5),
    updated_at TEXT NOT NULL CHECK(length(updated_at) = 27 AND updated_at >= created_at),
    result_json TEXT CHECK(result_json IS NULL OR CASE WHEN json_valid(result_json)
        THEN json_type(result_json) = 'object'
        AND length(CAST(result_json AS BLOB)) <= 1024 ELSE 0 END),
    CHECK((state = 'Pending' AND version = 1 AND updated_at = created_at AND result_json IS NULL)
        OR (state = 'Authorized' AND version = 2 AND result_json IS NULL)
        OR (state = 'Executing' AND version = 3 AND result_json IS NULL)
        OR (state IN ('Succeeded', 'Failed') AND version IN (4, 5) AND result_json IS NOT NULL)
        OR (state = 'Unknown' AND version = 4 AND result_json IS NOT NULL)),
    UNIQUE(run_id, step_index), UNIQUE(execution_scope, effect_key)
) STRICT""",
    "action_authorizations": f"""CREATE TABLE action_authorizations (
    authorization_id TEXT PRIMARY KEY NOT NULL CHECK(length(authorization_id) = 36),
    action_id TEXT NOT NULL UNIQUE REFERENCES actions(action_id),
    subject_id TEXT NOT NULL CHECK(length(subject_id) BETWEEN 1 AND 128),
    spec_digest TEXT NOT NULL CHECK({HEX.format("spec_digest")}),
    policy_revision TEXT NOT NULL CHECK(length(policy_revision) BETWEEN 1 AND 64),
    authorized_at TEXT NOT NULL CHECK(length(authorized_at) = 27)
) STRICT""",
    "action_attempts": f"""CREATE TABLE action_attempts (
    attempt_id TEXT PRIMARY KEY NOT NULL CHECK(length(attempt_id) = 36),
    action_id TEXT NOT NULL UNIQUE REFERENCES actions(action_id),
    authorization_id TEXT NOT NULL REFERENCES action_authorizations(authorization_id),
    subject_id TEXT NOT NULL CHECK(length(subject_id) BETWEEN 1 AND 128),
    spec_digest TEXT NOT NULL CHECK({HEX.format("spec_digest")}),
    policy_revision TEXT NOT NULL CHECK(length(policy_revision) BETWEEN 1 AND 64),
    claimed_version INTEGER NOT NULL CHECK(claimed_version = 3),
    claimed_at TEXT NOT NULL CHECK(length(claimed_at) = 27)
) STRICT""",
    "action_command_receipts": f"""CREATE TABLE action_command_receipts (
    subject_id TEXT NOT NULL CHECK(length(subject_id) BETWEEN 1 AND 128),
    operation TEXT NOT NULL CHECK(operation IN ('admit', 'authorize', 'execute')),
    resource_id TEXT NOT NULL CHECK(length(resource_id) = 36),
    idempotency_key TEXT NOT NULL CHECK(length(idempotency_key) = 36),
    body_digest TEXT NOT NULL CHECK({HEX.format("body_digest")}),
    accepted_action_id TEXT NOT NULL REFERENCES actions(action_id),
    accepted_version INTEGER NOT NULL CHECK(accepted_version BETWEEN 1 AND 3),
    accepted_at TEXT NOT NULL CHECK(length(accepted_at) = 27),
    PRIMARY KEY(subject_id, operation, resource_id, idempotency_key)
) STRICT""",
    "action_events": f"""CREATE TABLE action_events (
    event_id INTEGER PRIMARY KEY,
    action_id TEXT NOT NULL REFERENCES actions(action_id),
    action_version INTEGER NOT NULL CHECK(action_version BETWEEN 1 AND 5),
    event_type TEXT NOT NULL CHECK(event_type IN ('ACTION_CREATED', 'ACTION_AUTHORIZED',
        'EXECUTION_CLAIMED', 'EXECUTION_SUCCEEDED', 'EXECUTION_FAILED', 'EXECUTION_UNKNOWN')),
    created_at TEXT NOT NULL CHECK(length(created_at) = 27),
    spec_digest TEXT NOT NULL CHECK({HEX.format("spec_digest")}),
    safe_payload_json TEXT NOT NULL CHECK(CASE WHEN json_valid(safe_payload_json)
        THEN json_type(safe_payload_json) = 'object'
        AND length(CAST(safe_payload_json AS BLOB)) <= 1024 ELSE 0 END),
    UNIQUE(action_id, action_version)
) STRICT""",
}
TABLES = V1_TABLES | ADDITIONS


def objects(connection):
    return {
        (r[0], r[1], r[2], normalize_ddl(r[3]) if r[3] else None)
        for r in connection.execute("SELECT type, name, tbl_name, sql FROM sqlite_schema")
    }


@lru_cache(maxsize=1)
def expected_objects():
    # Ephemeral DDL reference only, never a fallback store for application data.
    with closing(sqlite3.connect(":memory:")) as reference:
        for ddl in TABLES.values():
            reference.execute(ddl)
        return objects(reference)


def validate_schema(connection, *, migrating=False):
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version != (1 if migrating else 2) or objects(connection) != expected_objects():
        raise RunStoreError(RunErrorCode.DATABASE_SCHEMA_UNSUPPORTED)


def initialize_database(path: Path):
    connection = None
    primary_error = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = (
            open_existing(path)
            if path.exists()
            else sqlite3.connect(path, isolation_level=None, timeout=BUSY_TIMEOUT_MS / 1000)
        )
        if connection.execute("PRAGMA user_version").fetchone()[0] == 0 and not objects(connection):
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("PRAGMA user_version").fetchone()[0] != 0 or objects(connection):
                raise RunStoreError(RunErrorCode.DATABASE_SCHEMA_UNSUPPORTED)
            for ddl in TABLES.values():
                connection.execute(ddl)
            connection.execute("PRAGMA user_version = 2")
            validate_schema(connection)
            connection.commit()
        validate_schema(connection)
        if connection.execute("PRAGMA journal_mode = WAL").fetchone()[0].lower() != "wal":
            raise RunStoreError(RunErrorCode.DATABASE_UNAVAILABLE)
        configure_connection(connection)
    except RunStoreError as error:
        primary_error = error
        raise
    except (sqlite3.Error, OSError):
        primary_error = RunStoreError(RunErrorCode.DATABASE_UNAVAILABLE)
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
                        RunStoreError(RunErrorCode.DATABASE_UNAVAILABLE)
                        if isinstance(cleanup_error, Exception)
                        else cleanup_error
                    )
                    primary_error.add_note("SQLite initialization cleanup failed.")
                    raise primary_error from None
                primary_error.add_note("SQLite initialization cleanup failed.")
