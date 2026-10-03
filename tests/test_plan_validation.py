import copy
import json
from dataclasses import FrozenInstanceError

import pytest

from agentic_intent_gate.domain.contracts import Intent, Risk
from agentic_intent_gate.domain.validation import ValidationFailure


@pytest.mark.parametrize("text", ["Read service-demo", "a" * 8192, "检查 service-demo"])
def test_valid_intent(validator, text):
    assert validator.parse_intent(json.dumps({"text": text}).encode()).text == text


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"text": ""},
        {"text": " \t\n"},
        {"text": 1},
        {"text": None},
        {"text": "a" * 8193},
        {"text": "界" * 3000},
        {"text": "x", "provider": "remote"},
        {"text": "\ud800"},
    ],
)
def test_invalid_intent(validator, body):
    with pytest.raises(ValidationFailure):
        validator.parse_intent(json.dumps(body).encode())


@pytest.mark.parametrize(
    "raw",
    [
        "",
        " ",
        "{",
        "{} trailing",
        "before {}",
        "{} {}",
        "```json\n{}\n```",
        "[]",
        "null",
        "12",
        '{"goal":"a","goal":"b"}',
        '{"steps":[{"arguments":{"enabled":true,"enabled":false}}]}',
        '{"revision":NaN}',
        '{"revision":Infinity}',
        '{"revision":1.0}',
        '{"revision":1e0}',
        '{"revision":9223372036854775808}',
        '{"goal":"\\ud800"}',
        "[" * 1100 + "]" * 1100,
    ],
)
def test_strict_plan_parser_rejects_untrusted_text(validator, raw):
    with pytest.raises(ValidationFailure):
        validator.parse_plan(raw)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p.pop("goal"),
        lambda p: p.update(goal=" "),
        lambda p: p.update(goal="a" * 513),
        lambda p: p.update(risk_class="Information disclosure risk"),
        lambda p: p.update(risk_class="LOW"),
        lambda p: p.update(approved=True),
        lambda p: p.update(steps=[]),
        lambda p: p.update(steps=["read"]),
        lambda p: p.update(steps=p["steps"] * 6),
        lambda p: p["steps"][0].update(tool_name="run_shell"),
        lambda p: p["steps"][0].update(tool_version="2"),
        lambda p: p["steps"][0].update(tool_version=1),
        lambda p: p["steps"][0]["arguments"].update(url="https://example.com"),
        lambda p: p.update(target="service-demo\n"),
    ],
)
def test_schema_rejections(validator, valid_plan, mutation):
    mutation(valid_plan)
    with pytest.raises(ValidationFailure) as caught:
        validator.parse_plan(json.dumps(valid_plan))
    assert caught.value.issues[0].code.startswith("SCHEMA_")


def mutation_step(tool="append_service_note", **arguments):
    args = {"service_id": "service-demo", "note": "A preview note"}
    if tool == "set_maintenance_mode":
        args = {"service_id": "service-demo", "enabled": True, "expected_revision": 1}
    return {"tool_name": tool, "tool_version": "1", "arguments": args | arguments}


@pytest.mark.parametrize(
    "risk,steps",
    [
        ("SIDE_EFFECT", [mutation_step()]),
        ("APPROVAL_REQUIRED", [mutation_step("set_maintenance_mode")]),
        ("APPROVAL_REQUIRED", [mutation_step(), mutation_step("set_maintenance_mode")]),
    ],
)
def test_valid_mutation_proposals_are_not_execution(validator, valid_plan, risk, steps):
    valid_plan.update(risk_class=risk, steps=steps)
    plan = validator.parse_plan(json.dumps(valid_plan))
    assert plan.risk_class == Risk(risk)
    assert plan.to_dict() == valid_plan
    assert not hasattr(plan, "execute")


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("downgrade", "SEMANTIC_RISK_MISMATCH"),
        ("overstate", "SEMANTIC_RISK_MISMATCH"),
        ("target_mismatch", "SEMANTIC_TARGET_MISMATCH"),
        ("unknown_target", "SEMANTIC_UNKNOWN_TARGET"),
        ("read_cap", "SEMANTIC_STEP_COUNT_LIMIT"),
        ("mutation_cap", "SEMANTIC_STEP_COUNT_LIMIT"),
        ("duplicate_mutation", "SEMANTIC_DUPLICATE_MUTATION"),
        ("read_after_mutation", "SEMANTIC_READ_AFTER_MUTATION"),
        ("wrong_revision", "SEMANTIC_REVISION_MISMATCH"),
        ("argument_bytes", "SEMANTIC_ARGUMENT_SIZE_LIMIT"),
    ],
)
def test_semantic_rejections(validator, valid_plan, kind, expected):
    read = copy.deepcopy(valid_plan["steps"][0])
    if kind == "downgrade":
        valid_plan["steps"] = [mutation_step()]
    elif kind == "overstate":
        valid_plan["risk_class"] = "SIDE_EFFECT"
    elif kind == "target_mismatch":
        valid_plan["steps"][0]["arguments"]["service_id"] = "other"
    elif kind == "unknown_target":
        valid_plan["target"] = "other"
        valid_plan["steps"][0]["arguments"]["service_id"] = "other"
    elif kind == "read_cap":
        valid_plan["steps"] = [read] * 4
    elif kind == "mutation_cap":
        valid_plan.update(
            steps=[mutation_step(note=str(i)) for i in range(3)], risk_class="SIDE_EFFECT"
        )
    elif kind == "duplicate_mutation":
        valid_plan.update(steps=[mutation_step()] * 2, risk_class="SIDE_EFFECT")
    elif kind == "read_after_mutation":
        valid_plan.update(steps=[mutation_step(), read], risk_class="SIDE_EFFECT")
    elif kind == "wrong_revision":
        valid_plan.update(
            steps=[mutation_step("set_maintenance_mode", expected_revision=2)],
            risk_class="APPROVAL_REQUIRED",
        )
    elif kind == "argument_bytes":
        valid_plan.update(steps=[mutation_step(note="😀" * 1024)], risk_class="SIDE_EFFECT")
    with pytest.raises(ValidationFailure) as caught:
        validator.parse_plan(json.dumps(valid_plan, ensure_ascii=False))
    assert caught.value.issues[0].code == expected


@pytest.mark.parametrize("revision", [True, False, 0, -1, 1.5, 1.0, "1"])
def test_revision_never_coerced(validator, valid_plan, revision):
    valid_plan.update(
        steps=[mutation_step("set_maintenance_mode", expected_revision=revision)],
        risk_class="APPROVAL_REQUIRED",
    )
    with pytest.raises(ValidationFailure):
        validator.parse_plan(json.dumps(valid_plan))


def test_plan_and_catalog_are_immutable(validator, valid_plan):
    plan = validator.parse_plan(json.dumps(valid_plan))
    with pytest.raises(FrozenInstanceError):
        plan.goal = "changed"
    with pytest.raises(TypeError):
        plan.steps[0].arguments["service_id"] = "other"
    with pytest.raises(TypeError):
        validator.tools[0].input_schema["required"] = []
    with pytest.raises(TypeError):
        validator.plan_schema["required"] = []
    wire = plan.to_dict()
    wire["steps"][0]["arguments"]["service_id"] = "other"
    assert plan.steps[0].arguments["service_id"] == "service-demo"


def test_model_text_byte_limit(validator):
    with pytest.raises(ValidationFailure) as caught:
        validator.parse_plan(" " * (16 * 1024 + 1))
    assert caught.value.issues[0].code == "PARSE_SIZE_LIMIT"


def test_intent_service_validation_is_independent(validator):
    with pytest.raises(ValidationFailure):
        validator.validate_intent(Intent(""))
