"""Short admission transactions over explicit v2; no authorization, claims or tool I/O."""

from contextlib import contextmanager
from uuid import uuid4

from agentic_intent_gate.application.action_compiler import (
    BUDGET_COMPILER_REVISION,
    ActionCompiler,
    ToolBinding,
)
from agentic_intent_gate.domain.action import (
    Action,
    ActionCode,
    ActionError,
    ActionState,
    CompiledSpec,
)
from agentic_intent_gate.domain.approval_binding import BindingError, digest
from agentic_intent_gate.domain.contracts import Risk
from agentic_intent_gate.domain.execution_budget import binding_timeout
from agentic_intent_gate.domain.run import RunErrorCode, RunStoreError
from agentic_intent_gate.domain.run_validation import (
    canonical_json,
    parse_timestamp,
    utc_now,
    valid_uuid,
)
from agentic_intent_gate.domain.validation import fail, strict_object
from agentic_intent_gate.infrastructure.persistence.sqlite_schema import (
    configure_connection,
    open_existing,
)
from agentic_intent_gate.infrastructure.persistence.sqlite_v2_runs import (
    binding_for,
    linked_decision,
)
from agentic_intent_gate.infrastructure.persistence.sqlite_v2_schema import validate_schema
from agentic_intent_gate.ports.action_repository import AdmissionCommand, AdmissionResult


def creation_payload(action):
    return {
        "from_state": None,
        "to_state": "Pending",
        "version_before": 0,
        "version_after": 1,
        "effect_key": action.effect_key,
    }


def action_from_row(row):
    values = dict(row)
    arguments = strict_object(values.pop("normalized_arguments_json"), max_bytes=4096)
    if canonical_json(arguments) != row["normalized_arguments_json"]:
        fail("ACTION_ARGUMENTS_NOT_CANONICAL")
    result_raw = values.pop("result_json")
    lifecycle = {
        key: values.pop(key)
        for key in ("action_id", "effect_key", "created_at", "state", "version", "updated_at")
    }
    spec = CompiledSpec(
        **values | {"risk_class": Risk(values["risk_class"]), "normalized_arguments": arguments}
    )
    result = strict_object(result_raw, max_bytes=1024) if result_raw is not None else None
    if result is not None and canonical_json(result) != result_raw:
        fail("ACTION_RESULT_NOT_CANONICAL")
    return Action(
        spec=spec, result=result, **lifecycle | {"state": ActionState(lifecycle["state"])}
    )


def validated_action(connection, runs, row, compiler=None):
    action = action_from_row(row)
    run = runs._load(connection, action.spec.run_id)
    if run is None:
        fail("ACTION_RUN_MISSING")
    decision = linked_decision(connection, run)
    binding = binding_for(connection, decision)
    # Historical integrity is independent of the CURRENT executable registry. Retained
    # metadata supplies the versioned hash input, not a dispatch permission or callable.
    spec = action.spec
    try:
        retained = ToolBinding(
            spec.tool_name,
            spec.tool_version,
            spec.adapter_id,
            spec.binding_revision,
            spec.target,
            spec.execution_scope,
        )
    except ActionError:
        fail("ACTION_RETAINED_TOOL_CONTRACT")
    try:
        budget = (
            binding_timeout(spec.binding_revision)
            if spec.compiler_revision == BUDGET_COMPILER_REVISION
            else 5
        )
        compiler = ActionCompiler(runs.validator, (retained,), execution_timeout_seconds=budget)
        compiler.validate_spec(action.spec, run, decision, binding)
    except (ActionError, ValueError):
        # Invalid RETAINED identity is storage corruption, not current-registry retirement.
        # Admission still reports current binding denial separately; HTTP must never turn
        # a tampered durable row into a harmless eligibility conflict.
        fail("ACTION_RETAINED_TOOL_CONTRACT")
    if action.created_at < run.updated_at:
        fail("ACTION_SPEC_LINK")
    from agentic_intent_gate.infrastructure.persistence.sqlite_action_ledger import validate_history

    return validate_history(connection, action)


def validate_ledger(connection, runs, compiler=None):
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        fail("ACTION_FOREIGN_KEYS")
    for row in connection.execute("SELECT * FROM actions ORDER BY action_id").fetchall():
        validated_action(connection, runs, row, compiler)


class SQLiteActionRepository:
    def __init__(self, runs, compiler: ActionCompiler, *, clock=utc_now):
        self.runs, self.path, self.compiler, self._clock = runs, runs.path, compiler, clock
        if compiler.validator is not runs.validator:
            raise ActionError(ActionCode.DATABASE_UNAVAILABLE)
        self.validate_all()

    @contextmanager
    def _transaction(self, *, write=False):
        connection = None
        primary_error = None
        try:
            connection = open_existing(self.path)
            configure_connection(connection)
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            validate_schema(connection)
            yield connection
            connection.commit()
        except (ActionError, BindingError, RunStoreError) as error:
            primary_error = error
            raise
        except Exception:
            # No guessed successful receipt after uncertain commit; caller can explicitly look up.
            primary_error = ActionError(ActionCode.DATABASE_UNAVAILABLE)
            raise primary_error from None
        except BaseException as error:
            # Re-raise cancellation/control flow; ordinary cleanup faults must not replace it.
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
                    # Preserve established safe errors; cleanup-only uncertainty is not success.
                    if primary_error is None:
                        raise ActionError(ActionCode.DATABASE_UNAVAILABLE) from None
                    primary_error.add_note("Action transaction cleanup failed.")

    def validate_all(self):
        with self._transaction() as connection:
            if [r[0] for r in connection.execute("PRAGMA integrity_check")] != ["ok"]:
                fail("ACTION_INTEGRITY")
            validate_ledger(connection, self.runs, self.compiler)

    def _load(self, connection, action_id):
        row = connection.execute("SELECT * FROM actions WHERE action_id=?", (action_id,)).fetchone()
        return validated_action(connection, self.runs, row, self.compiler) if row else None

    def get(self, action_id):
        if not valid_uuid(action_id):
            raise ActionError(ActionCode.INVALID_REQUEST)
        with self._transaction() as connection:
            return self._load(connection, action_id)

    def list_for_run(self, run_id):
        if not valid_uuid(run_id):
            raise ActionError(ActionCode.INVALID_REQUEST)
        with self._transaction() as connection:
            if self.runs._load(connection, run_id) is None:
                raise RunStoreError(RunErrorCode.RUN_NOT_FOUND, run_id)
            rows = connection.execute(
                "SELECT * FROM actions WHERE run_id=? ORDER BY step_index", (run_id,)
            ).fetchall()
            return tuple(
                validated_action(connection, self.runs, row, self.compiler) for row in rows
            )

    def _insert_action(self, connection, action):
        values = action.spec.to_dict()
        values["normalized_arguments_json"] = canonical_json(values.pop("normalized_arguments"))
        values.update(
            action_id=action.action_id,
            effect_key=action.effect_key,
            created_at=action.created_at,
            state="Pending",
            version=1,
            updated_at=action.created_at,
            result_json=None,
        )
        names = ",".join(values)
        placeholders = ",".join("?" for _ in values)
        connection.execute(
            f"INSERT INTO actions({names}) VALUES ({placeholders})", tuple(values.values())
        )

    def admit(self, command, *, principal=None, policy=None):
        AdmissionCommand(**vars(command))
        if policy is not None:
            policy.require(principal, "admit")
            if command.subject_id != principal.subject_id:
                raise ActionError(ActionCode.NOT_AUTHORIZED)
        fingerprint = digest({"expected_run_version": command.expected_run_version})
        with self._transaction(write=True) as connection:
            receipt = connection.execute(
                "SELECT * FROM action_command_receipts WHERE subject_id=? AND operation=? "
                "AND resource_id=? AND idempotency_key=?",
                (command.subject_id, "admit", command.run_id, command.idempotency_key),
            ).fetchone()
            if receipt and receipt["body_digest"] != fingerprint:
                raise ActionError(ActionCode.IDEMPOTENCY_CONFLICT)
            # Proof is always revalidated, including accepted replay; no model/tool I/O.
            run = self.runs._load(connection, command.run_id)
            if run is None:
                raise RunStoreError(RunErrorCode.RUN_NOT_FOUND, command.run_id)
            decision = linked_decision(connection, run)
            if decision is None:
                raise ActionError(ActionCode.RUN_NOT_ELIGIBLE)
            binding = binding_for(connection, decision)
            spec = self.compiler.compile(run, decision, binding)
            if policy is not None:
                policy.permit(principal, "admit", spec)
            if receipt:
                action = self._load(connection, receipt["accepted_action_id"])
                if action is None or action.spec != spec:
                    fail("ADMISSION_REPLAY_SPEC")
                return AdmissionResult(action, replayed=True, created=False)
            if run.version != command.expected_run_version:
                raise RunStoreError(RunErrorCode.STALE_RUN_VERSION, command.run_id)
            existing = connection.execute(
                "SELECT * FROM actions WHERE run_id=? AND step_index=0", (run.run_id,)
            ).fetchone()
            if existing:
                action = validated_action(connection, self.runs, existing, self.compiler)
                if action.spec != spec:
                    fail("ADMISSION_EXISTING_SPEC")
            else:
                now = self._clock()
                parse_timestamp(now)
                action = Action(str(uuid4()), spec, str(uuid4()), max(now, run.updated_at))
                self._insert_action(connection, action)
                connection.execute(
                    "INSERT INTO action_events(action_id, action_version, event_type, "
                    "created_at, spec_digest, safe_payload_json) VALUES (?,1,?,?,?,?)",
                    (
                        action.action_id,
                        "ACTION_CREATED",
                        action.created_at,
                        spec.spec_digest,
                        canonical_json(creation_payload(action)),
                    ),
                )
            accepted_at = max(self._clock(), action.created_at)
            parse_timestamp(accepted_at)
            connection.execute(
                "INSERT INTO action_command_receipts VALUES (?,?,?,?,?,?,?,?)",
                (
                    command.subject_id,
                    "admit",
                    command.run_id,
                    command.idempotency_key,
                    fingerprint,
                    action.action_id,
                    1,
                    accepted_at,
                ),
            )
            result = self._load(connection, action.action_id)
            return AdmissionResult(result, replayed=False, created=existing is None)
