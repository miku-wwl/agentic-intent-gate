"""Pure retained grant/claim/result consistency; no current policy or tool lookup."""

from dataclasses import replace

from agentic_intent_gate.domain.action import ActionState, identifier
from agentic_intent_gate.domain.approval_binding import digest
from agentic_intent_gate.domain.contracts import thaw_json
from agentic_intent_gate.domain.run_validation import canonical_json, parse_timestamp, valid_uuid
from agentic_intent_gate.domain.validation import fail, strict_object


def edge_payload(before, after, version, grant=None, attempt=None, result=None, source=None):
    value = {
        "from_state": before,
        "to_state": after,
        "version_before": version - 1,
        "version_after": version,
    }
    if grant:
        value.update(
            authorization_id=grant["authorization_id"],
            subject_id=grant["subject_id"],
            policy_revision=grant["policy_revision"],
        )
    if attempt:
        value["attempt_id"] = attempt["attempt_id"]
    if result:
        value.update(
            result_code=result["code"], receipt_reference=result["receipt_reference"], source=source
        )
    return value


def validate_history(connection, action):
    from agentic_intent_gate.adapters.sqlite_actions import creation_payload

    grants = connection.execute(
        "SELECT * FROM action_authorizations WHERE action_id=?", (action.action_id,)
    ).fetchall()
    attempts = connection.execute(
        "SELECT * FROM action_attempts WHERE action_id=?", (action.action_id,)
    ).fetchall()
    if (len(grants), len(attempts)) != (int(action.version >= 2), int(action.version >= 3)):
        fail("ACTION_LINK_COUNT")
    if action.version > 4:
        fail("ACTION_RECONCILIATION_NOT_IMPLEMENTED")
    grant, attempt = (
        (dict(grants[0]) if grants else None),
        (dict(attempts[0]) if attempts else None),
    )
    if grant:
        parse_timestamp(grant["authorized_at"])
        if (
            not valid_uuid(grant["authorization_id"])
            or not identifier(grant["subject_id"], limit=128)
            or not identifier(grant["policy_revision"])
            or grant["spec_digest"] != action.spec.spec_digest
            or grant["authorized_at"] < action.created_at
        ):
            fail("ACTION_GRANT_LINK")
        action = replace(
            action,
            authorization={
                k: grant[k]
                for k in ("authorization_id", "subject_id", "policy_revision", "authorized_at")
            },
        )
    if attempt:
        parse_timestamp(attempt["claimed_at"])
        if (
            not valid_uuid(attempt["attempt_id"])
            or attempt["authorization_id"] != grant["authorization_id"]
            or any(attempt[k] != grant[k] for k in ("subject_id", "spec_digest", "policy_revision"))
            or attempt["claimed_version"] != 3
            or attempt["claimed_at"] < grant["authorized_at"]
        ):
            fail("ACTION_ATTEMPT_LINK")
    events = connection.execute(
        "SELECT * FROM action_events WHERE action_id=? ORDER BY action_version", (action.action_id,)
    ).fetchall()
    if len(events) != action.version:
        fail("ACTION_EVENT_COUNT")
    expected = [("ACTION_CREATED", action.created_at, creation_payload(action))]
    if grant:
        expected.append(
            (
                "ACTION_AUTHORIZED",
                grant["authorized_at"],
                edge_payload("Pending", "Authorized", 2, grant),
            )
        )
    if attempt:
        expected.append(
            (
                "EXECUTION_CLAIMED",
                attempt["claimed_at"],
                edge_payload("Authorized", "Executing", 3, grant, attempt),
            )
        )
    if action.version == 4:
        last = strict_object(events[-1]["safe_payload_json"], max_bytes=1024)
        source = last.get("source")
        if source not in {"adapter", "recovery"} or (
            source == "recovery" and action.state is not ActionState.Unknown
        ):
            fail("ACTION_RESULT_SOURCE")
        event_type = {
            ActionState.Succeeded: "EXECUTION_SUCCEEDED",
            ActionState.Failed: "EXECUTION_FAILED",
            ActionState.Unknown: "EXECUTION_UNKNOWN",
        }[action.state]
        expected.append(
            (
                event_type,
                action.updated_at,
                edge_payload(
                    "Executing",
                    action.state.value,
                    4,
                    grant,
                    attempt,
                    thaw_json(action.result),
                    source,
                ),
            )
        )
    previous_time, previous_id = action.created_at, 0
    for version, (event, (kind, timestamp, payload)) in enumerate(
        zip(events, expected, strict=True), 1
    ):
        parse_timestamp(event["created_at"])
        if (
            event["action_version"] != version
            or event["event_type"] != kind
            or event["event_id"] <= previous_id
            or event["created_at"] != timestamp
            or timestamp < previous_time
            or event["spec_digest"] != action.spec.spec_digest
            or event["safe_payload_json"] != canonical_json(payload)
        ):
            fail("ACTION_EVENT_LINK")
        previous_time, previous_id = timestamp, event["event_id"]
    if action.updated_at != previous_time:
        fail("ACTION_FINAL_TIME")
    receipts = connection.execute(
        "SELECT * FROM action_command_receipts WHERE accepted_action_id=?", (action.action_id,)
    ).fetchall()
    counts = {"admit": 0, "authorize": 0, "execute": 0}
    for receipt in receipts:
        op = receipt["operation"]
        counts[op] += 1
        parse_timestamp(receipt["accepted_at"])
        if not valid_uuid(receipt["idempotency_key"]) or not identifier(
            receipt["subject_id"], limit=128
        ):
            fail("ACTION_RECEIPT_ID")
        if op == "admit":
            resource, version, body, timestamp = (
                action.spec.run_id,
                1,
                {"expected_run_version": 5},
                action.created_at,
            )
            if receipt["accepted_at"] < timestamp:
                fail("ACTION_RECEIPT_TIME")
        else:
            fact = grant if op == "authorize" else attempt
            if fact is None:
                fail("ACTION_RECEIPT_FACT")
            version = 2 if op == "authorize" else 3
            resource, body = action.action_id, {"expected_action_version": version - 1}
            timestamp = fact["authorized_at" if op == "authorize" else "claimed_at"]
            if receipt["subject_id"] != fact["subject_id"] or receipt["accepted_at"] != timestamp:
                fail("ACTION_RECEIPT_SUBJECT_TIME")
        if (
            receipt["resource_id"] != resource
            or receipt["accepted_version"] != version
            or receipt["body_digest"] != digest(body)
        ):
            fail("ACTION_RECEIPT_LINK")
    if counts["admit"] < 1 or (counts["authorize"], counts["execute"]) != (
        int(bool(grant)),
        int(bool(attempt)),
    ):
        fail("ACTION_RECEIPT_COUNT")
    return action
