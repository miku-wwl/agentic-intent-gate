"""Independent state matrix and strict snapshot/decision contract boundaries."""

import json
from dataclasses import FrozenInstanceError, replace
from itertools import product

import pytest
from run_helpers import advance

from agentic_intent_gate.domain.approval import (
    ApprovalChoice,
    ApprovalDecisionRequest,
    requires_approval,
)
from agentic_intent_gate.domain.contracts import ErrorCode, Risk
from agentic_intent_gate.domain.run import (
    RUN_MESSAGES,
    TERMINAL_STATES,
    RunError,
    RunErrorCode,
    RunState,
    RunStoreError,
    validate_transition,
)
from agentic_intent_gate.domain.run_validation import check_contract, contract_validator
from agentic_intent_gate.domain.validation import ValidationFailure

EXPECTED_EDGES = {
    "IntentReceived": {"PlanGenerating", "PlanRejected"},
    "PlanGenerating": {"PlanValidated", "PlanRejected"},
    "PlanValidated": {"Ready", "AwaitingApproval"},
    "AwaitingApproval": {"Approved", "Rejected"},
}


@pytest.mark.parametrize("before,after", list(product(RunState, repeat=2)))
def test_exhaustive_transition_matrix(before, after):
    if after.value in EXPECTED_EDGES.get(before.value, set()):
        validate_transition(before, after)
    else:
        with pytest.raises(RunStoreError) as caught:
            validate_transition(before, after)
        assert caught.value.code == RunErrorCode.INVALID_RUN_TRANSITION


@pytest.mark.parametrize("state", list(RunState))
def test_valid_snapshots_and_immutable_values(run_repository, state):
    risk = (
        Risk.SIDE_EFFECT
        if state in {RunState.AwaitingApproval, RunState.Approved, RunState.Rejected}
        else Risk.READ_ONLY
    )
    run = advance(run_repository, state, risk)
    check_contract("day2-run.schema.json", run.to_dict())
    assert (state in TERMINAL_STATES) == (
        state.value in {"Ready", "Approved", "Rejected", "PlanRejected"}
    )
    assert run.intent.text == "  Accepted Intent 😀\n"
    with pytest.raises(FrozenInstanceError):
        run.version = 100
    with pytest.raises(FrozenInstanceError):
        run.intent.text = "changed"
    if run.plan:
        with pytest.raises(FrozenInstanceError):
            run.plan.goal = "changed"
        with pytest.raises(TypeError):
            run.plan.steps[0].arguments["service_id"] = "changed"
        wire = run.to_dict()
        wire["plan"]["steps"][0]["arguments"]["service_id"] = "changed"
        assert run.plan.steps[0].arguments["service_id"] == "service-demo"


@pytest.mark.parametrize("state", list(RunState))
@pytest.mark.parametrize("bad_version", [0, 6, True, 4.0])
def test_wrong_state_versions_fail(run_repository, state, bad_version):
    risk = (
        Risk.SIDE_EFFECT
        if state in {RunState.AwaitingApproval, RunState.Approved, RunState.Rejected}
        else Risk.READ_ONLY
    )
    with pytest.raises(ValidationFailure):
        replace(advance(run_repository, state, risk), version=bad_version)


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-02-29T00:00:00.000000Z",
        "2026-13-01T00:00:00.000000Z",
        "2026-10-06T24:00:00.000000Z",
        "2026-10-06T00:00:60.000000Z",
        "2026-10-06T00:00:00Z",
        "2026-10-06T00:00:00.000000+00:00",
        "2026-10-06T00:00:00.000000Z\n",
        "0000-01-01T00:00:00.000000Z",
    ],
)
def test_calendar_and_exact_timestamp_format(run_repository, timestamp):
    with pytest.raises(ValidationFailure):
        replace(advance(run_repository), created_at=timestamp)


def test_time_order_and_risk_plan_field_combinations(run_repository):
    run = advance(run_repository)
    for changes in (
        {"updated_at": "2000-01-01T00:00:00.000000Z"},
        {"risk_class": Risk.SIDE_EFFECT},
        {"plan": None},
        {"error": RunError(ErrorCode.INTERNAL_ERROR)},
        {"run_id": run.run_id.upper()},
        {"run_id": "a" * 36},
    ):
        with pytest.raises(ValidationFailure):
            replace(run, **changes)


@pytest.mark.parametrize(
    "risk,required",
    [
        (Risk.READ_ONLY, False),
        (Risk.SIDE_EFFECT, True),
        (Risk.APPROVAL_REQUIRED, True),
    ],
)
def test_one_policy_and_approval_required(run_repository, risk, required):
    assert requires_approval(risk) is required
    run = advance(run_repository, RunState.AwaitingApproval if required else RunState.Ready, risk)
    assert run.approval_required is required
    if required:
        decided = run_repository.decide_approval(
            run.run_id, ApprovalDecisionRequest(ApprovalChoice.REJECT, 4)
        )
        assert decided.approval_required is True and decided.error is None


@pytest.mark.parametrize("risk", [None, "READ_ONLY", "LOW", 0, False])
def test_unknown_policy_values_fail_closed(risk):
    with pytest.raises(ValidationFailure):
        requires_approval(risk)


@pytest.mark.parametrize(
    "code",
    [
        ErrorCode.PLAN_VALIDATION_ERROR,
        ErrorCode.MODEL_UNAVAILABLE,
        ErrorCode.MODEL_TIMEOUT,
        ErrorCode.MODEL_RESPONSE_ERROR,
        ErrorCode.INTERNAL_ERROR,
        RunErrorCode.RECOVERY_INTERRUPTED,
    ],
)
def test_fixed_safe_errors(code):
    assert RunError(code).to_dict() == {"code": code.value, "message": RUN_MESSAGES[code]}
    assert len(RUN_MESSAGES[code]) <= 256


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"decision": "APPROVE"},
        {"decision": "ALLOW", "expected_version": 4},
        *(
            {"decision": "APPROVE", "expected_version": v}
            for v in [True, False, None, "4", 0, -1, 4.0, 9007199254740992]
        ),
        *(
            {"decision": "APPROVE", "expected_version": 4, "reason": r}
            for r in [None, 1, "x" * 1025, "\x00", "\x08", "\x0b", "\x0c", "\x1f", "\x7f", "\ud800"]
        ),
        *(
            {"decision": "APPROVE", "expected_version": 4, field: "override"}
            for field in [
                "actor_type",
                "actor_id",
                "run_id",
                "plan",
                "risk_class",
                "approval_required",
                "endpoint",
                "provider",
                "execute",
            ]
        ),
    ],
)
def test_invalid_decision_contract(body):
    with pytest.raises(ValidationFailure):
        ApprovalDecisionRequest.parse(json.dumps(body).encode())


@pytest.mark.parametrize(
    "raw",
    [
        b'{"decision":"APPROVE","decision":"REJECT","expected_version":4}',
        b'{"decision":"APPROVE","expected_version":4e0}',
        b"[]",
        b"null",
        b"\xff",
        b'{"decision":"APPROVE","expected_version":NaN}',
        b"{} {}",
    ],
)
def test_strict_decision_decoding(raw):
    with pytest.raises(ValidationFailure):
        ApprovalDecisionRequest.parse(raw)


@pytest.mark.parametrize("reason", [None, "", "\t\r\n", "😀" * 1024])
def test_reason_omitted_empty_and_unicode_limits(reason):
    body = {"decision": "REJECT", "expected_version": 9007199254740991}
    if reason is not None:
        body["reason"] = reason
    request = ApprovalDecisionRequest.parse(json.dumps(body).encode())
    assert request.reason == reason
    assert request.decision == ApprovalChoice.REJECT


def test_schema_references_resolve_locally_without_network(run_repository, monkeypatch, tmp_path):
    import socket

    def forbidden_network(*args, **kwargs):
        raise AssertionError("Network schema resolution is forbidden")

    monkeypatch.setattr(socket, "create_connection", forbidden_network)
    monkeypatch.setattr(socket.socket, "connect", forbidden_network)
    monkeypatch.chdir(tmp_path)
    contract_validator.cache_clear()
    for risk in Risk:
        state = RunState.Ready if risk == Risk.READ_ONLY else RunState.AwaitingApproval
        check_contract("day2-run.schema.json", advance(run_repository, state, risk).to_dict())
    ApprovalDecisionRequest.parse(b'{"decision":"APPROVE","expected_version":4}')
