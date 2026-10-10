"""Explicit v2 composition of the frozen named Run operations."""

from contextlib import contextmanager

from agentic_intent_gate.config.configuration import resolve_database_path
from agentic_intent_gate.domain.approval import ApprovalChoice, ApprovalDecision
from agentic_intent_gate.domain.approval_binding import ApprovalBinding, BindingCode, BindingError
from agentic_intent_gate.domain.run import RunErrorCode, RunState, RunStoreError
from agentic_intent_gate.domain.run_validation import canonical_json, parse_timestamp, utc_now
from agentic_intent_gate.domain.validation import fail, strict_object
from agentic_intent_gate.infrastructure.persistence.sqlite_run_repository import (
    EVENT_EDGES,
    SQLiteRunRepository,
)
from agentic_intent_gate.infrastructure.persistence.sqlite_schema import (
    configure_connection,
    open_existing,
)
from agentic_intent_gate.infrastructure.persistence.sqlite_v2_schema import (
    initialize_database,
    validate_schema,
)


def linked_decision(connection, run):
    rows = connection.execute(
        "SELECT * FROM approval_decisions WHERE run_id=?", (run.run_id,)
    ).fetchall()
    if len(rows) != int(run.state in {RunState.Approved, RunState.Rejected}):
        fail("STORED_DECISION_COUNT")
    if not rows:
        return None
    decision = ApprovalDecision(**dict(rows[0]) | {"decision": ApprovalChoice(rows[0]["decision"])})
    if decision.created_at != run.updated_at or (decision.decision == ApprovalChoice.APPROVE) != (
        run.state == RunState.Approved
    ):
        fail("STORED_DECISION_LINK")
    return decision


def binding_for(connection, decision):
    row = connection.execute(
        "SELECT * FROM approval_plan_bindings WHERE decision_id=?", (decision.decision_id,)
    ).fetchone()
    if row is None:
        raise BindingError(BindingCode.MISSING)
    return ApprovalBinding(**dict(row))


class SQLiteV2RunRepository(SQLiteRunRepository):
    def __init__(self, path, validator, *, clock=utc_now):
        self.path = resolve_database_path(path)
        self.validator = validator
        self._clock = clock
        initialize_database(self.path)
        self.validate_all()

    @contextmanager
    def _transaction(self, *, write=False, run_id=None):
        connection = None
        primary_error = None
        try:
            connection = open_existing(self.path)
            configure_connection(connection)
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            validate_schema(connection)
            yield connection
            connection.commit()
        except (BindingError, RunStoreError) as error:
            # Admission consumes this typed failure; generic Run reads translate below.
            primary_error = error
            raise
        except Exception:
            primary_error = RunStoreError(RunErrorCode.DATABASE_UNAVAILABLE, run_id)
            raise primary_error from None
        except BaseException as error:
            # Cancellation/control flow must also survive a secondary cleanup failure.
            primary_error = error
            raise
        finally:
            if connection is not None:
                try:
                    try:
                        if connection.in_transaction:
                            connection.rollback()
                    finally:
                        connection.close()
                except Exception:
                    # Never replace a primary domain/storage error or acknowledge failed cleanup.
                    if primary_error is None:
                        raise RunStoreError(RunErrorCode.DATABASE_UNAVAILABLE, run_id) from None

    def _validate_history(self, connection, run):
        decision = linked_decision(connection, run)
        if decision:
            # Validate lifecycle projection WITHOUT reusing current Plan as past proof.
            events = connection.execute(
                "SELECT * FROM audit_events WHERE run_id=? ORDER BY run_version", (run.run_id,)
            ).fetchall()
            if len(events) != 5:
                fail("STORED_AUDIT_COUNT")
            previous, previous_time, previous_id = None, run.created_at, 0
            for version, event in enumerate(events, 1):
                payload = strict_object(event["safe_payload_json"], max_bytes=1024)
                state = RunState(payload["to_state"])
                parse_timestamp(event["created_at"])
                if (
                    event["run_version"] != version
                    or event["event_id"] <= previous_id
                    or event["created_at"] < previous_time
                ):
                    fail("STORED_AUDIT_VERSION_OR_TIME")
                if previous is None:
                    if (
                        event["event_type"] != "RUN_CREATED"
                        or state != RunState.IntentReceived
                        or event["created_at"] != run.created_at
                    ):
                        fail("STORED_CREATION_EVENT")
                elif (previous, state) not in EVENT_EDGES.get(event["event_type"], set()):
                    fail("STORED_AUDIT_EDGE")
                expected = {
                    "from_state": previous.value if previous else None,
                    "to_state": state.value,
                    "version_before": version - 1,
                    "version_after": version,
                    "risk_class": run.risk_class.value if version >= 3 else None,
                    "error_code": None,
                    "decision_id": decision.decision_id if version == 5 else None,
                }
                if event["safe_payload_json"] != canonical_json(expected):
                    fail("STORED_AUDIT_PAYLOAD")
                previous, previous_time, previous_id = state, event["created_at"], event["event_id"]
            if previous != run.state or previous_time != run.updated_at:
                fail("STORED_AUDIT_FINAL_STATE")
            binding_for(connection, decision).verify(decision, run.plan, self.validator)
        super()._validate_history(connection, run)

    def _append_event(self, connection, before, after, event_type, decision_id=None):
        # Base transaction has inserted its decision and CAS Run, but not yet loaded end state.
        if decision_id:
            decision = linked_decision(connection, after)
            binding = ApprovalBinding.bound(decision, after.plan, self.validator)
            connection.execute(
                "INSERT INTO approval_plan_bindings VALUES (?, ?, ?, ?, ?)",
                tuple(vars(binding).values()),
            )
        super()._append_event(connection, before, after, event_type, decision_id)

    def validate_all(self):
        try:
            super().validate_all()
            # Validate all v2 facts before recovery/serving, even when only Run APIs are wired.
            from agentic_intent_gate.infrastructure.persistence.sqlite_actions import (
                validate_ledger,
            )

            with self._transaction() as connection:
                validate_ledger(connection, self)
        except BindingError:
            raise RunStoreError(RunErrorCode.DATABASE_UNAVAILABLE) from None

    def get(self, run_id):
        try:
            return super().get(run_id)
        except BindingError:
            raise RunStoreError(RunErrorCode.DATABASE_UNAVAILABLE, run_id) from None

    def decide_approval(self, run_id, request):
        try:
            return super().decide_approval(run_id, request)
        except BindingError:
            raise RunStoreError(RunErrorCode.DATABASE_UNAVAILABLE, run_id) from None
