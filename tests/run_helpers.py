"""Controlled Day 2 fixtures; all persistence uses real temporary disk files."""

import json
import sqlite3
from contextlib import closing
from uuid import uuid4

from agentic_intent_gate.domain.approval import ApprovalChoice, ApprovalDecisionRequest
from agentic_intent_gate.domain.contracts import ErrorCode, Intent, Risk
from agentic_intent_gate.domain.run import RunState
from agentic_intent_gate.domain.run_validation import utc_now


def plan_for(validator, risk=Risk.READ_ONLY):
    tool, arguments = {
        Risk.READ_ONLY: ("get_service_record", {"service_id": "service-demo"}),
        Risk.SIDE_EFFECT: (
            "append_service_note",
            {"service_id": "service-demo", "note": "note 😀"},
        ),
        Risk.APPROVAL_REQUIRED: (
            "set_maintenance_mode",
            {"service_id": "service-demo", "enabled": True, "expected_revision": 1},
        ),
    }[risk]
    return validator.parse_plan(
        json.dumps(
            {
                "goal": "Controlled proposal",
                "target": "service-demo",
                "risk_class": risk.value,
                "steps": [{"tool_name": tool, "tool_version": "1", "arguments": arguments}],
            }
        )
    )


def advance(repository, state=RunState.Ready, risk=Risk.READ_ONLY, run_id=None):
    run = repository.create(run_id or str(uuid4()), Intent("  Accepted Intent 😀\n"), utc_now())
    if state == RunState.IntentReceived:
        return run
    if state == RunState.PlanRejected:
        return repository.reject_planning(run.run_id, 1, ErrorCode.MODEL_TIMEOUT)
    run = repository.start_generation(run.run_id, 1)
    if state == RunState.PlanGenerating:
        return run
    run = repository.save_validated_plan(run.run_id, 2, plan_for(repository.validator, risk))
    if state == RunState.PlanValidated:
        return run
    run = repository.route_validated_plan(run.run_id, 3)
    if state in {RunState.Ready, RunState.AwaitingApproval}:
        assert run.state == state
        return run
    choice = ApprovalChoice.APPROVE if state == RunState.Approved else ApprovalChoice.REJECT
    return repository.decide_approval(run.run_id, ApprovalDecisionRequest(choice, 4))


def database_rows(path):
    with closing(sqlite3.connect(path)) as connection:
        return {
            table: connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
            for table in ("runs", "approval_decisions", "audit_events")
        }
