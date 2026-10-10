"""File-backed Run operations with per-call connections and atomic state/audit CAS."""

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from agentic_intent_gate.config.configuration import resolve_database_path
from agentic_intent_gate.domain.approval import (
    ApprovalChoice,
    ApprovalDecision,
    ApprovalDecisionRequest,
    requires_approval,
)
from agentic_intent_gate.domain.contracts import ErrorCode, Intent, Plan, Risk
from agentic_intent_gate.domain.run import (
    Run,
    RunError,
    RunErrorCode,
    RunState,
    RunStoreError,
    validate_transition,
)
from agentic_intent_gate.domain.run_validation import (
    canonical_json,
    parse_timestamp,
    utc_now,
)
from agentic_intent_gate.domain.validation import PLAN_BYTES, PlanValidator, fail, strict_object
from agentic_intent_gate.infrastructure.persistence.sqlite_schema import (
    configure_connection,
    initialize_database,
    open_existing,
    validate_schema,
)

EVENT_EDGES = {
    "PLAN_GENERATION_STARTED": {(RunState.IntentReceived, RunState.PlanGenerating)},
    "PLAN_VALIDATED": {(RunState.PlanGenerating, RunState.PlanValidated)},
    "PLAN_REJECTED": {
        (RunState.IntentReceived, RunState.PlanRejected),
        (RunState.PlanGenerating, RunState.PlanRejected),
    },
    "RECOVERY_REJECTED": {
        (RunState.IntentReceived, RunState.PlanRejected),
        (RunState.PlanGenerating, RunState.PlanRejected),
    },
    "RUN_READY": {(RunState.PlanValidated, RunState.Ready)},
    "APPROVAL_REQUIRED": {(RunState.PlanValidated, RunState.AwaitingApproval)},
    "RECOVERY_ROUTED": {
        (RunState.PlanValidated, RunState.Ready),
        (RunState.PlanValidated, RunState.AwaitingApproval),
    },
    "APPROVED": {(RunState.AwaitingApproval, RunState.Approved)},
    "REJECTED": {(RunState.AwaitingApproval, RunState.Rejected)},
}


def audit_payload(before: Run | None, after: Run, decision_id: str | None = None) -> dict:
    return {
        "from_state": before.state.value if before else None,
        "to_state": after.state.value,
        "version_before": before.version if before else 0,
        "version_after": after.version,
        "risk_class": after.risk_class.value if after.risk_class else None,
        "error_code": after.error.code.value if after.error else None,
        "decision_id": decision_id,
    }


class SQLiteRunRepository:
    def __init__(
        self, path: str | Path, validator: PlanValidator, *, clock: Callable[[], str] = utc_now
    ) -> None:
        self.path = resolve_database_path(path)
        self.validator = validator
        self._clock = clock
        initialize_database(self.path)
        self.validate_all()

    @contextmanager
    def _transaction(
        self, *, write: bool = False, run_id: str | None = None
    ) -> Iterator[sqlite3.Connection]:
        connection = None
        primary_error = None
        try:
            connection = open_existing(self.path)
            configure_connection(connection)
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            validate_schema(connection)
            yield connection
            connection.commit()
        except RunStoreError as exc:
            primary_error = RunStoreError(exc.code, run_id or exc.run_id)
            raise primary_error from None
        except Exception:
            # A failed/unconfirmed COMMIT is never turned into a guessed successful Run.
            primary_error = RunStoreError(RunErrorCode.DATABASE_UNAVAILABLE, run_id)
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
                            RunStoreError(RunErrorCode.DATABASE_UNAVAILABLE, run_id)
                            if isinstance(cleanup_error, Exception)
                            else cleanup_error
                        )
                        primary_error.add_note("Run transaction cleanup failed.")
                        raise primary_error from None
                    primary_error.add_note("Run transaction cleanup failed.")

    def _canonical_plan(self, plan: Plan) -> tuple[Plan, str]:
        raw = canonical_json(plan.to_dict())
        validated = self.validator.parse_plan(raw)
        return validated, raw

    def _load(self, connection: sqlite3.Connection, run_id: str) -> Run | None:
        row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        intent = Intent(row["intent"])
        self.validator.validate_intent(intent)
        raw = row["validated_plan_json"]
        plan = self.validator.parse_plan(raw) if raw is not None else None
        if plan is not None and (
            canonical_json(plan.to_dict()) != raw or len(raw.encode()) > PLAN_BYTES
        ):
            fail("STORED_PLAN_NOT_CANONICAL")
        code = row["last_error_code"]
        error = None
        if code is not None:
            error = RunError(
                RunErrorCode.RECOVERY_INTERRUPTED
                if code == "RECOVERY_INTERRUPTED"
                else ErrorCode(code)
            )
        if row["last_error_message"] != (error.to_dict()["message"] if error else None):
            fail("STORED_ERROR")
        run = Run(
            row["run_id"],
            intent,
            RunState(row["state"]),
            plan,
            Risk(row["risk_class"]) if row["risk_class"] is not None else None,
            row["version"],
            row["created_at"],
            row["updated_at"],
            error,
        )
        self._validate_history(connection, run)
        return run

    def _validate_history(self, connection: sqlite3.Connection, run: Run) -> None:
        decisions = connection.execute(
            "SELECT * FROM approval_decisions WHERE run_id = ?", (run.run_id,)
        ).fetchall()
        terminal_decision = run.state in {RunState.Approved, RunState.Rejected}
        if len(decisions) != int(terminal_decision):
            fail("STORED_DECISION_COUNT")
        decision = (
            ApprovalDecision(
                **dict(decisions[0]) | {"decision": ApprovalChoice(decisions[0]["decision"])}
            )
            if decisions
            else None
        )
        if decision is not None and (
            decision.created_at != run.updated_at
            or (decision.decision == ApprovalChoice.APPROVE) != (run.state == RunState.Approved)
        ):
            fail("STORED_DECISION_LINK")
        events = connection.execute(
            "SELECT * FROM audit_events WHERE run_id = ? ORDER BY run_version", (run.run_id,)
        ).fetchall()
        if len(events) != run.version:
            fail("STORED_AUDIT_COUNT")
        before = None
        previous_event_id = 0
        for version, event in enumerate(events, 1):
            payload = strict_object(event["safe_payload_json"], max_bytes=1024)
            state = RunState(payload["to_state"])
            if event["run_version"] != version or event["event_id"] <= previous_event_id:
                fail("STORED_AUDIT_VERSION")
            event_type = event["event_type"]
            if before is None:
                if event_type != "RUN_CREATED" or state != RunState.IntentReceived:
                    fail("STORED_CREATION_EVENT")
                if event["created_at"] != run.created_at:
                    fail("STORED_CREATION_TIME")
            elif (before.state, state) not in EVENT_EDGES.get(event_type, set()):
                fail("STORED_AUDIT_EDGE")
            if event_type == "RECOVERY_REJECTED" and (
                run.error is None or run.error.code != RunErrorCode.RECOVERY_INTERRUPTED
            ):
                fail("STORED_RECOVERY_ERROR")
            if event_type == "PLAN_REJECTED" and (
                run.error is None or run.error.code == RunErrorCode.RECOVERY_INTERRUPTED
            ):
                fail("STORED_PLANNING_ERROR")
            has_plan = state not in {
                RunState.IntentReceived,
                RunState.PlanGenerating,
                RunState.PlanRejected,
            }
            after = Run(
                run.run_id,
                run.intent,
                state,
                run.plan if has_plan else None,
                run.risk_class if has_plan else None,
                version,
                run.created_at,
                event["created_at"],
                run.error if state == RunState.PlanRejected else None,
            )
            if before is not None and after.updated_at < before.updated_at:
                fail("STORED_AUDIT_TIME_ORDER")
            linked_id = decision.decision_id if decision and version == 5 else None
            expected = audit_payload(before, after, linked_id)
            if payload != expected or event["safe_payload_json"] != canonical_json(expected):
                fail("STORED_AUDIT_PAYLOAD")
            before = after
            previous_event_id = event["event_id"]
        if before != run:
            fail("STORED_AUDIT_FINAL_STATE")

    def validate_all(self) -> None:
        with self._transaction() as connection:
            if [row[0] for row in connection.execute("PRAGMA integrity_check")] != ["ok"]:
                fail("STORED_INTEGRITY")
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                fail("STORED_FOREIGN_KEYS")
            for row in connection.execute("SELECT run_id FROM runs ORDER BY run_id").fetchall():
                self._load(connection, row[0])

    def get(self, run_id: str) -> Run | None:
        with self._transaction(run_id=run_id) as connection:
            run = self._load(connection, run_id)
        return run

    def recovery_candidates(self) -> tuple[Run, ...]:
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT run_id FROM runs WHERE state IN "
                "('IntentReceived', 'PlanGenerating', 'PlanValidated') ORDER BY run_id"
            ).fetchall()
            runs = tuple(self._load(connection, row[0]) for row in rows)
        return runs

    def _append_event(
        self,
        connection: sqlite3.Connection,
        before: Run | None,
        after: Run,
        event_type: str,
        decision_id: str | None = None,
    ) -> None:
        payload = canonical_json(audit_payload(before, after, decision_id))
        if len(payload.encode("utf-8")) > 1024:
            fail("AUDIT_SIZE_LIMIT")
        connection.execute(
            "INSERT INTO audit_events(run_id, run_version, event_type, created_at, "
            "safe_payload_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (after.run_id, after.version, event_type, after.updated_at, payload),
        )

    def create(self, run_id: str, intent: Intent, created_at: str) -> Run:
        with self._transaction(write=True, run_id=run_id) as connection:
            self.validator.validate_intent(intent)
            run = Run(
                run_id, intent, RunState.IntentReceived, None, None, 1, created_at, created_at
            )
            connection.execute(
                "INSERT INTO runs(run_id, intent, state, version, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, intent.text, run.state.value, 1, created_at, created_at),
            )
            self._append_event(connection, None, run, "RUN_CREATED")
            result = self._load(connection, run_id)
        return result

    def _guard(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        expected_version: int,
        states: set[RunState],
    ) -> Run:
        run = self._load(connection, run_id)
        if run is None:
            raise RunStoreError(RunErrorCode.RUN_NOT_FOUND, run_id)
        if type(expected_version) is not int or run.version != expected_version:
            raise RunStoreError(RunErrorCode.STALE_RUN_VERSION, run_id)
        if run.state not in states:
            raise RunStoreError(RunErrorCode.INVALID_RUN_TRANSITION, run_id)
        return run

    def _next(self, before: Run, state: RunState, **changes) -> Run:
        validate_transition(before.state, state)
        now = self._clock()
        parse_timestamp(now)
        return replace(
            before,
            state=state,
            version=before.version + 1,
            updated_at=max(now, before.updated_at),
            **changes,
        )

    def _commit_transition(
        self,
        connection: sqlite3.Connection,
        before: Run,
        after: Run,
        event_type: str,
        decision: ApprovalDecision | None = None,
    ) -> Run:
        cursor = connection.execute(
            "UPDATE runs SET state=?, version=?, updated_at=?, validated_plan_json=?, "
            "risk_class=?, last_error_code=?, last_error_message=? "
            "WHERE run_id=? AND state=? AND version=?",
            (
                after.state.value,
                after.version,
                after.updated_at,
                canonical_json(after.plan.to_dict()) if after.plan else None,
                after.risk_class.value if after.risk_class else None,
                after.error.code.value if after.error else None,
                after.error.to_dict()["message"] if after.error else None,
                before.run_id,
                before.state.value,
                before.version,
            ),
        )
        if cursor.rowcount != 1:
            raise RunStoreError(RunErrorCode.STALE_RUN_VERSION, before.run_id)
        if decision is not None:
            connection.execute(
                "INSERT INTO approval_decisions(decision_id, run_id, decision, reason, actor_type, "
                "created_at, run_version_before, run_version_after) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    decision.decision_id,
                    decision.run_id,
                    decision.decision.value,
                    decision.reason,
                    decision.actor_type,
                    decision.created_at,
                    decision.run_version_before,
                    decision.run_version_after,
                ),
            )
        self._append_event(
            connection, before, after, event_type, decision.decision_id if decision else None
        )
        return self._load(connection, after.run_id)

    def start_generation(self, run_id: str, expected_version: int) -> Run:
        with self._transaction(write=True, run_id=run_id) as connection:
            before = self._guard(connection, run_id, expected_version, {RunState.IntentReceived})
            after = self._next(before, RunState.PlanGenerating)
            result = self._commit_transition(connection, before, after, "PLAN_GENERATION_STARTED")
        return result

    def save_validated_plan(self, run_id: str, expected_version: int, plan: Plan) -> Run:
        with self._transaction(write=True, run_id=run_id) as connection:
            before = self._guard(connection, run_id, expected_version, {RunState.PlanGenerating})
            validated, _ = self._canonical_plan(plan)
            after = self._next(
                before, RunState.PlanValidated, plan=validated, risk_class=validated.risk_class
            )
            result = self._commit_transition(connection, before, after, "PLAN_VALIDATED")
        return result

    def reject_planning(self, run_id: str, expected_version: int, error_code: ErrorCode) -> Run:
        if not isinstance(error_code, ErrorCode):
            raise RunStoreError(RunErrorCode.INVALID_RUN_TRANSITION, run_id)
        return self._reject(run_id, expected_version, error_code, "PLAN_REJECTED")

    def recover_interrupted(self, run_id: str, expected_version: int) -> Run:
        return self._reject(
            run_id, expected_version, RunErrorCode.RECOVERY_INTERRUPTED, "RECOVERY_REJECTED"
        )

    def _reject(
        self, run_id: str, expected_version: int, code: ErrorCode | RunErrorCode, event_type: str
    ) -> Run:
        with self._transaction(write=True, run_id=run_id) as connection:
            before = self._guard(
                connection,
                run_id,
                expected_version,
                {RunState.IntentReceived, RunState.PlanGenerating},
            )
            after = self._next(before, RunState.PlanRejected, error=RunError(code))
            result = self._commit_transition(connection, before, after, event_type)
        return result

    def route_validated_plan(
        self, run_id: str, expected_version: int, *, recovery: bool = False
    ) -> Run:
        with self._transaction(write=True, run_id=run_id) as connection:
            before = self._guard(connection, run_id, expected_version, {RunState.PlanValidated})
            needed = requires_approval(before.risk_class)
            after = self._next(before, RunState.AwaitingApproval if needed else RunState.Ready)
            event_type = (
                "RECOVERY_ROUTED" if recovery else "APPROVAL_REQUIRED" if needed else "RUN_READY"
            )
            result = self._commit_transition(connection, before, after, event_type)
        return result

    def decide_approval(self, run_id: str, request: ApprovalDecisionRequest) -> Run:
        with self._transaction(write=True, run_id=run_id) as connection:
            # Revalidate constructed domain requests too, rather than relying only on HTTP parsing.
            ApprovalDecisionRequest(request.decision, request.expected_version, request.reason)
            before = self._guard(
                connection, run_id, request.expected_version, {RunState.AwaitingApproval}
            )
            approved = request.decision == ApprovalChoice.APPROVE
            after = self._next(before, RunState.Approved if approved else RunState.Rejected)
            decision = ApprovalDecision(
                str(uuid4()),
                run_id,
                request.decision,
                request.reason,
                "LOCAL_USER",
                after.updated_at,
                before.version,
                after.version,
            )
            result = self._commit_transition(
                connection, before, after, "APPROVED" if approved else "REJECTED", decision
            )
        return result
