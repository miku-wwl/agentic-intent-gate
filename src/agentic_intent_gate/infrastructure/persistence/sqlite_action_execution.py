"""Named short CAS writes. A claim permit is returned only AFTER confirmed commit."""

from uuid import uuid4

from agentic_intent_gate.application.action_compiler import ToolBinding
from agentic_intent_gate.domain.action import (
    ActionCode,
    ActionError,
    ActionState,
    guard_action,
    safe_result,
)
from agentic_intent_gate.domain.approval_binding import digest
from agentic_intent_gate.domain.run_validation import canonical_json, parse_timestamp, valid_uuid
from agentic_intent_gate.infrastructure.persistence.sqlite_action_ledger import edge_payload
from agentic_intent_gate.infrastructure.persistence.sqlite_actions import SQLiteActionRepository
from agentic_intent_gate.ports.action_repository import ActionCommand, ClaimResult, CommandResult


class SQLiteExecutionRepository(SQLiteActionRepository):
    def _eligible(self, action):
        s = action.spec
        binding = ToolBinding(
            s.tool_name,
            s.tool_version,
            s.adapter_id,
            s.binding_revision,
            s.target,
            s.execution_scope,
        )
        if (
            binding not in self.compiler.bindings
            or s.compiler_revision != self.compiler.compiler_revision_for(binding)
        ):
            raise ActionError(ActionCode.TOOL_NOT_BOUND)

    def _checked(self, connection, command, principal, policy, operation):
        action = self._load(connection, command.action_id)
        if action is None:
            raise ActionError(ActionCode.NOT_FOUND)
        self._eligible(action)
        revision = policy.permit(principal, operation, action.spec)
        return action, revision

    def _receipt(self, connection, command, principal, operation):
        body = digest({"expected_action_version": command.expected_action_version})
        row = connection.execute(
            "SELECT * FROM action_command_receipts WHERE subject_id=? AND operation=? "
            "AND resource_id=? AND idempotency_key=?",
            (principal.subject_id, operation, command.action_id, command.idempotency_key),
        ).fetchone()
        if row and row["body_digest"] != body:
            raise ActionError(ActionCode.IDEMPOTENCY_CONFLICT)
        return row, body

    def _accept(self, connection, command, principal, operation, body, version, now):
        connection.execute(
            "INSERT INTO action_command_receipts VALUES (?,?,?,?,?,?,?,?)",
            (
                principal.subject_id,
                operation,
                command.action_id,
                command.idempotency_key,
                body,
                command.action_id,
                version,
                now,
            ),
        )

    def _edge(
        self, connection, action, state, version, now, grant, attempt=None, result=None, source=None
    ):
        event_type = {
            ActionState.Authorized: "ACTION_AUTHORIZED",
            ActionState.Executing: "EXECUTION_CLAIMED",
            ActionState.Succeeded: "EXECUTION_SUCCEEDED",
            ActionState.Failed: "EXECUTION_FAILED",
            ActionState.Unknown: "EXECUTION_UNKNOWN",
        }[state]
        changed = connection.execute(
            "UPDATE actions SET state=?,version=?,updated_at=?,result_json=? "
            "WHERE action_id=? AND state=? AND version=?",
            (
                state.value,
                version,
                now,
                canonical_json(result) if result else None,
                action.action_id,
                action.state.value,
                action.version,
            ),
        ).rowcount
        if changed != 1:
            raise ActionError(ActionCode.STALE_VERSION)
        payload = edge_payload(
            action.state.value, state.value, version, grant, attempt, result, source
        )
        connection.execute(
            "INSERT INTO action_events(action_id,action_version,event_type,created_at,"
            "spec_digest,safe_payload_json) VALUES (?,?,?,?,?,?)",
            (
                action.action_id,
                version,
                event_type,
                now,
                action.spec.spec_digest,
                canonical_json(payload),
            ),
        )

    def _now(self, action):
        now = max(self._clock(), action.updated_at)
        parse_timestamp(now)
        return now

    def authorize(self, command, principal, policy):
        ActionCommand(**vars(command))
        policy.require(principal, "authorize")
        with self._transaction(write=True) as connection:
            action, revision = self._checked(connection, command, principal, policy, "authorize")
            receipt, body = self._receipt(connection, command, principal, "authorize")
            if receipt:
                if (
                    action.authorization["subject_id"],
                    action.authorization["policy_revision"],
                ) != (principal.subject_id, revision):
                    raise ActionError(ActionCode.NOT_AUTHORIZED)
                result = CommandResult(action, True)
            else:
                guard_action(action, command.expected_action_version, {ActionState.Pending})
                now = self._now(action)
                grant = {
                    "authorization_id": str(uuid4()),
                    "action_id": action.action_id,
                    "subject_id": principal.subject_id,
                    "spec_digest": action.spec.spec_digest,
                    "policy_revision": revision,
                    "authorized_at": now,
                }
                connection.execute(
                    "INSERT INTO action_authorizations VALUES (?,?,?,?,?,?)", tuple(grant.values())
                )
                self._edge(connection, action, ActionState.Authorized, 2, now, grant)
                self._accept(connection, command, principal, "authorize", body, 2, now)
                result = CommandResult(self._load(connection, action.action_id), False)
        return result

    def claim(self, command, principal, policy):
        ActionCommand(**vars(command))
        policy.require(principal, "execute")
        with self._transaction(write=True) as connection:
            action, revision = self._checked(connection, command, principal, policy, "execute")
            grant_row = connection.execute(
                "SELECT * FROM action_authorizations WHERE action_id=?", (action.action_id,)
            ).fetchone()
            grant = dict(grant_row) if grant_row else None
            if grant and (grant["subject_id"], grant["policy_revision"], grant["spec_digest"]) != (
                principal.subject_id,
                revision,
                action.spec.spec_digest,
            ):
                raise ActionError(ActionCode.NOT_AUTHORIZED)
            receipt, body = self._receipt(connection, command, principal, "execute")
            if receipt:
                result = ClaimResult(action, True)
            else:
                guard_action(action, command.expected_action_version, set(ActionState))
                if action.version >= 3:
                    raise ActionError(ActionCode.ALREADY_STARTED)
                guard_action(action, command.expected_action_version, {ActionState.Authorized})
                now, attempt_id = self._now(action), str(uuid4())
                attempt = {
                    "attempt_id": attempt_id,
                    "action_id": action.action_id,
                    "authorization_id": grant["authorization_id"],
                    "subject_id": principal.subject_id,
                    "spec_digest": action.spec.spec_digest,
                    "policy_revision": revision,
                    "claimed_version": 3,
                    "claimed_at": now,
                }
                connection.execute(
                    "INSERT INTO action_attempts VALUES (?,?,?,?,?,?,?,?)", tuple(attempt.values())
                )
                self._edge(connection, action, ActionState.Executing, 3, now, grant, attempt)
                self._accept(connection, command, principal, "execute", body, 3, now)
                result = ClaimResult(self._load(connection, action.action_id), False, attempt_id)
        # Exiting the transaction confirmed COMMIT. Exceptions, even post-commit, yield no permit.
        return result

    def _finish(self, connection, action, attempt_id, state, result, source):
        guard_action(action, 3, {ActionState.Executing})
        attempt = dict(
            connection.execute(
                "SELECT * FROM action_attempts WHERE action_id=?", (action.action_id,)
            ).fetchone()
        )
        if attempt["attempt_id"] != attempt_id or attempt["claimed_version"] != action.version:
            raise ActionError(ActionCode.NOT_EXECUTABLE)
        grant = dict(
            connection.execute(
                "SELECT * FROM action_authorizations WHERE action_id=?", (action.action_id,)
            ).fetchone()
        )
        self._edge(connection, action, state, 4, self._now(action), grant, attempt, result, source)
        return self._load(connection, action.action_id)

    def finish(self, action_id, attempt_id, result):
        if not valid_uuid(action_id) or not valid_uuid(attempt_id):
            raise ActionError(ActionCode.INVALID_REQUEST)
        state = {
            "EXECUTION_SUCCEEDED": ActionState.Succeeded,
            "EXECUTION_CONFIRMED_FAILED": ActionState.Failed,
            "EXECUTION_OUTCOME_UNKNOWN": ActionState.Unknown,
        }.get(result.get("code"))
        if state is None or result not in (
            safe_result(state, result.get("receipt_reference")),
            safe_result(
                state, result.get("receipt_reference"), compiler_revision="action-compiler-v2"
            ),
        ):
            raise ActionError(ActionCode.INVALID_REQUEST)
        with self._transaction(write=True) as connection:
            action = self._load(connection, action_id)
            if action is None:
                raise ActionError(ActionCode.NOT_FOUND)
            if result != safe_result(
                state,
                result.get("receipt_reference"),
                compiler_revision=action.spec.compiler_revision,
            ):
                raise ActionError(ActionCode.INVALID_REQUEST)
            return self._finish(connection, action, attempt_id, state, result, "adapter")

    def recover(self, *, owners_stopped=False):
        if owners_stopped is not True:
            raise ActionError(ActionCode.NOT_AUTHORIZED)
        self.validate_all()
        changed = 0
        with self._transaction(write=True) as connection:
            for row in connection.execute(
                "SELECT action_id FROM actions WHERE state='Executing' ORDER BY action_id"
            ).fetchall():
                action = self._load(connection, row[0])
                attempt = connection.execute(
                    "SELECT attempt_id FROM action_attempts WHERE action_id=?", (row[0],)
                ).fetchone()
                self._finish(
                    connection,
                    action,
                    attempt[0],
                    ActionState.Unknown,
                    safe_result(ActionState.Unknown),
                    "recovery",
                )
                changed += 1
        return changed
