"""Strict parsing plus the canonical schema and trusted semantic rules."""

import json
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from importlib.resources import files
from itertools import islice
from pathlib import Path

from jsonschema import Draft202012Validator

from agentic_intent_gate.domain.contracts import (
    Intent,
    Plan,
    PlanStep,
    Risk,
    Tool,
    ValidationIssue,
    freeze_json,
    thaw_json,
)

REQUEST_BYTES = 16 * 1024
INTENT_BYTES = 8 * 1024
PLAN_BYTES = 16 * 1024
ARGUMENT_BYTES = 4 * 1024


class ValidationFailure(Exception):
    def __init__(self, issues: Sequence[ValidationIssue]):
        self.issues = tuple(issues[:10])
        super().__init__("Contract validation failed")


def fail(code: str, path: str = "/") -> None:
    raise ValidationFailure((ValidationIssue(path, code),))


def _object_pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            fail("PARSE_DUPLICATE_KEY")
        result[key] = value
    return result


def _reject_float(_value: str) -> None:
    fail("PARSE_NUMBER_NOT_INTEGER")


def _integer(value: str) -> int:
    number = int(value)
    if not -(2**63) <= number <= 2**63 - 1:
        fail("PARSE_INTEGER_RANGE")
    return number


def strict_object(raw: str | bytes, *, max_bytes: int) -> dict:
    try:
        if isinstance(raw, bytes):
            if len(raw) > max_bytes:
                fail("PARSE_SIZE_LIMIT")
            text = raw.decode("utf-8")
        elif isinstance(raw, str):
            if len(raw.encode("utf-8")) > max_bytes:
                fail("PARSE_SIZE_LIMIT")
            text = raw
        else:
            fail("PARSE_EXPECTED_TEXT")
        value = json.loads(
            text,
            object_pairs_hook=_object_pairs,
            parse_float=_reject_float,
            parse_constant=_reject_float,
            parse_int=_integer,
        )
        if not isinstance(value, dict):
            fail("PARSE_EXPECTED_OBJECT")
        # Check decoded Unicode too: escaped lone surrogates are not valid UTF-8 text.
        stack = [(value, 0)]
        while stack:
            item, depth = stack.pop()
            if depth > 64:
                fail("PARSE_DEPTH_LIMIT")
            if isinstance(item, dict):
                stack.extend((key, depth + 1) for key in item)
                stack.extend((part, depth + 1) for part in item.values())
            elif isinstance(item, list):
                stack.extend((part, depth + 1) for part in item)
            elif isinstance(item, str):
                item.encode("utf-8")
        return value
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ValidationFailure((ValidationIssue("/", "PARSE_INVALID_JSON"),)) from exc


def load_schema(name: str) -> dict:
    packaged = files("agentic_intent_gate").joinpath("schemas", name)
    if packaged.is_file():
        schema = json.loads(packaged.read_text("utf-8"))
    else:
        # Editable development uses the same root files that the wheel force-includes.
        schema = json.loads(
            (Path(__file__).resolve().parents[3] / "schemas" / name).read_text("utf-8")
        )
    Draft202012Validator.check_schema(schema)
    return schema


def _safe_path(parts: Sequence[object]) -> str:
    allowed = {
        "text",
        "goal",
        "target",
        "steps",
        "risk_class",
        "tool_name",
        "tool_version",
        "arguments",
        "service_id",
        "note",
        "enabled",
        "expected_revision",
    }
    return "/" + "/".join(
        str(part) if isinstance(part, int) or part in allowed else "field" for part in parts
    )


def _check_schema(validator: Draft202012Validator, value: object) -> None:
    issues = tuple(
        ValidationIssue(_safe_path(tuple(error.absolute_path)), "SCHEMA_" + error.validator.upper())
        for error in islice(validator.iter_errors(value), 10)
    )
    if issues:
        raise ValidationFailure(issues)


class PlanValidator:
    def __init__(
        self,
        tools: Sequence[Tool],
        *,
        intent_schema: dict,
        plan_schema: dict,
        target_revisions: Mapping[str, int],
        argument_rules: Mapping[tuple[str, str], Callable] | None = None,
    ) -> None:
        self.tools = tuple(tools)
        self._catalog = {(tool.name, tool.version): tool for tool in self.tools}
        if len(self._catalog) != len(self.tools):
            raise ValueError("Duplicate trusted tool definitions")
        self._targets = dict(target_revisions)
        self._argument_rules = dict(argument_rules) if argument_rules is not None else None
        if self._argument_rules is not None and set(self._argument_rules) != set(self._catalog):
            raise ValueError("Every trusted tool requires an explicit semantic rule")
        self.plan_schema = freeze_json(plan_schema)
        self._intent_validator = Draft202012Validator(deepcopy(intent_schema))
        self._plan_validator = Draft202012Validator(deepcopy(plan_schema))
        self._run_validator = None
        if self._argument_rules is not None:
            from agentic_intent_gate.domain.run_validation import build_contract_validator

            self._run_validator = build_contract_validator(
                "day2-run.schema.json", plan_schema=thaw_json(self.plan_schema)
            )

    def validate_run_snapshot(self, value):
        """Retain every Run schema rule; only its trusted Plan resource is composed."""
        if self._run_validator is None:
            from agentic_intent_gate.domain.run_validation import check_contract

            check_contract("day2-run.schema.json", value)
        else:
            _check_schema(self._run_validator, value)
            if value["plan"] is not None:
                self.parse_plan(json.dumps(value["plan"]))

    def parse_intent(self, raw: bytes) -> Intent:
        value = strict_object(raw, max_bytes=REQUEST_BYTES)
        _check_schema(self._intent_validator, value)
        intent = Intent(value["text"])
        self.validate_intent(intent)
        return intent

    def validate_intent(self, intent: Intent) -> None:
        _check_schema(self._intent_validator, {"text": intent.text})
        try:
            if len(intent.text.encode("utf-8")) > INTENT_BYTES:
                fail("INTENT_UTF8_SIZE_LIMIT", "/text")
        except UnicodeError as exc:
            raise ValidationFailure((ValidationIssue("/text", "INTENT_INVALID_UTF8"),)) from exc

    def parse_plan(self, raw: str) -> Plan:
        value = strict_object(raw, max_bytes=PLAN_BYTES)
        _check_schema(self._plan_validator, value)
        if value["target"] not in self._targets:
            fail("SEMANTIC_UNKNOWN_TARGET", "/target")
        read_count = mutation_count = 0
        mutation_seen = False
        mutation_keys = set()
        rank = {Risk.READ_ONLY: 0, Risk.SIDE_EFFECT: 1, Risk.APPROVAL_REQUIRED: 2}
        computed_risk = Risk.READ_ONLY
        for index, step in enumerate(value["steps"]):
            path = f"/steps/{index}"
            tool = self._catalog.get((step["tool_name"], step["tool_version"]))
            if tool is None:
                fail("SEMANTIC_UNTRUSTED_TOOL", path)
            arguments = step["arguments"]
            if self._argument_rules is None:
                if arguments["service_id"] != value["target"]:
                    fail("SEMANTIC_TARGET_MISMATCH", path + "/arguments/service_id")
            else:
                self._argument_rules[(tool.name, tool.version)](
                    freeze_json(arguments), value["target"]
                )
            canonical = json.dumps(
                arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            if len(canonical.encode("utf-8")) > ARGUMENT_BYTES:
                fail("SEMANTIC_ARGUMENT_SIZE_LIMIT", path + "/arguments")
            if tool.risk_class == Risk.READ_ONLY:
                read_count += 1
                if mutation_seen:
                    fail("SEMANTIC_READ_AFTER_MUTATION", path)
            else:
                mutation_seen = True
                mutation_count += 1
                key = (tool.name, tool.version, canonical)
                if key in mutation_keys:
                    fail("SEMANTIC_DUPLICATE_MUTATION", path)
                mutation_keys.add(key)
            if self._argument_rules is None and "expected_revision" in arguments:
                if arguments["expected_revision"] != self._targets[value["target"]]:
                    fail("SEMANTIC_REVISION_MISMATCH", path + "/arguments/expected_revision")
            if rank[tool.risk_class] > rank[computed_risk]:
                computed_risk = tool.risk_class
        if read_count > 3 or mutation_count > 2:
            fail("SEMANTIC_STEP_COUNT_LIMIT", "/steps")
        if value["risk_class"] != computed_risk.value:
            fail("SEMANTIC_RISK_MISMATCH", "/risk_class")
        return Plan(
            goal=value["goal"],
            target=value["target"],
            steps=tuple(PlanStep(**step) for step in value["steps"]),
            risk_class=computed_risk,
        )
