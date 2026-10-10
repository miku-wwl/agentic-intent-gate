"""Supported single-tool consumer composition. All registrations come from trusted host code."""

import json
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass

from agentic_intent_gate.api.local_http import create_app
from agentic_intent_gate.application.action_compiler import LEGACY_TOOL, ActionCompiler, ToolBinding
from agentic_intent_gate.application.action_execution import ActionExecutionGateway
from agentic_intent_gate.application.generate_plan import PlanningService
from agentic_intent_gate.application.recover_actions import recover_actions
from agentic_intent_gate.application.recover_runs import recover_runs
from agentic_intent_gate.application.runs import RunService
from agentic_intent_gate.config.configuration import resolve_database_path
from agentic_intent_gate.domain.action import ActionCode, ActionError, identifier
from agentic_intent_gate.domain.approval import ApprovalChoice, ApprovalDecisionRequest
from agentic_intent_gate.domain.contracts import Intent, Risk, Tool, freeze_json, thaw_json
from agentic_intent_gate.domain.validation import PlanValidator, fail, load_schema, strict_object
from agentic_intent_gate.infrastructure.persistence.sqlite_action_execution import (
    SQLiteExecutionRepository,
)
from agentic_intent_gate.infrastructure.persistence.sqlite_v2_runs import SQLiteV2RunRepository
from agentic_intent_gate.infrastructure.security.bound_capability import (
    BoundCapabilityAuthorization,
)
from agentic_intent_gate.infrastructure.security.local_capability import (
    CapabilityVerifier,
    provision_capability,
)
from agentic_intent_gate.infrastructure.security.single_owner import single_owner
from agentic_intent_gate.infrastructure.tools.executable_registry import ExecutableRegistry
from agentic_intent_gate.ports.action_repository import ActionCommand, AdmissionCommand
from agentic_intent_gate.ports.authorization import AuthorizationPort
from agentic_intent_gate.ports.model_provider import ModelProvider
from agentic_intent_gate.ports.tool_executor import (
    EXTERNAL_PAYLOAD_REVISION,
    AttemptContext,
    ExecutionSpec,
    Outcome,
    ToolExecutor,
    ToolOutcome,
)

__all__ = [
    "ActionCommand",
    "AdmissionCommand",
    "ApprovalChoice",
    "ApprovalDecisionRequest",
    "AttemptContext",
    "AuthorizationPort",
    "BoundCapabilityAuthorization",
    "CapabilityVerifier",
    "EXTERNAL_PAYLOAD_REVISION",
    "ExecutableRegistry",
    "ExecutionSpec",
    "Intent",
    "ModelProvider",
    "Outcome",
    "Runtime",
    "ToolBinding",
    "ToolCapability",
    "ToolExecutor",
    "ToolOutcome",
    "open_runtime",
    "provision_capability",
]


def _bounded_schema(schema):
    """Small supported JSON Schema subset: closed flat objects of bounded scalar inputs."""
    value = strict_object(json.dumps(thaw_json(schema)), max_bytes=4096)
    if (
        set(value) != {"type", "additionalProperties", "properties", "required"}
        or value["type"] != "object"
        or value["additionalProperties"] is not False
        or not isinstance(value["properties"], dict)
        or not 1 <= len(value["properties"]) <= 16
        or not isinstance(value["required"], list)
        or len(set(value["required"])) != len(value["required"])
        or set(value["required"]) != set(value["properties"])
    ):
        raise ValueError("A closed bounded scalar argument schema is required")
    for name, field in value["properties"].items():
        if not identifier(name) or not isinstance(field, dict):
            raise ValueError("Invalid argument definition")
        kind = field.get("type")
        if kind == "string":
            if (
                not set(field) <= {"type", "minLength", "maxLength", "pattern"}
                or type(field.get("minLength")) is not int
                or type(field.get("maxLength")) is not int
                or not 1 <= field["minLength"] <= field["maxLength"] <= 1024
            ):
                raise ValueError("Strings require explicit finite bounds")
        elif kind == "integer":
            if (
                set(field) != {"type", "minimum", "maximum"}
                or type(field["minimum"]) is not int
                or type(field["maximum"]) is not int
                or not -(2**63) <= field["minimum"] <= field["maximum"] <= 2**63 - 1
            ):
                raise ValueError("Integers require signed 64-bit bounds")
        elif kind != "boolean" or set(field) != {"type"}:
            raise ValueError("Only bounded string/integer/boolean arguments are supported")
    from jsonschema import Draft202012Validator

    Draft202012Validator.check_schema(value)
    return value


@dataclass(frozen=True)
class ToolCapability:
    """One reviewed SIDE_EFFECT tool/target; schema and optional semantics are host-owned."""

    binding: ToolBinding
    description: str
    input_schema: Mapping
    target_argument: str | None = None
    semantic_rule: Callable | None = None

    def __post_init__(self):
        if (
            type(self.binding) is not ToolBinding
            or (self.binding.tool_name, self.binding.tool_version, self.binding.target)
            == LEGACY_TOOL
            or not isinstance(self.description, str)
            or not 1 <= len(self.description) <= 512
        ):
            raise ValueError("An explicit non-demo tool binding is required")
        schema = _bounded_schema(self.input_schema)
        if self.target_argument is not None and (
            self.target_argument not in schema["properties"]
            or schema["properties"][self.target_argument]["type"] != "string"
        ):
            raise ValueError("target_argument must name a required string argument")
        if self.semantic_rule is not None and not callable(self.semantic_rule):
            raise ValueError("Semantic rules must be trusted host callables")
        object.__setattr__(self, "input_schema", freeze_json(schema))

    def build_validator(self):
        b = self.binding
        # Additive composition in memory: bundled schemas remain byte-for-byte unchanged.
        schema = load_schema("day1-plan.schema.json")
        schema["properties"]["target"] = {"const": b.target}
        schema["properties"]["steps"]["maxItems"] = 1
        schema["properties"]["risk_class"] = {"const": "SIDE_EFFECT"}
        schema["$defs"]["step"]["oneOf"] = [
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["tool_name", "tool_version", "arguments"],
                "properties": {
                    "tool_name": {"const": b.tool_name},
                    "tool_version": {"const": b.tool_version},
                    "arguments": thaw_json(self.input_schema),
                },
            }
        ]

        def semantics(arguments, target):
            if target != b.target or (
                self.target_argument is not None and arguments[self.target_argument] != target
            ):
                fail("SEMANTIC_TARGET_MISMATCH")
            if self.semantic_rule is not None and self.semantic_rule(arguments, target) is not None:
                fail("SEMANTIC_ARGUMENT_RULE")

        tool = Tool(
            b.tool_name, b.tool_version, self.description, self.input_schema, Risk.SIDE_EFFECT
        )
        return PlanValidator(
            (tool,),
            intent_schema=load_schema("day1-intent.schema.json"),
            plan_schema=schema,
            target_revisions={b.target: 1},
            argument_rules={(b.tool_name, b.tool_version): semantics},
        )


@dataclass(frozen=True)
class Runtime:
    planning: PlanningService
    runs: SQLiteV2RunRepository
    actions: SQLiteExecutionRepository
    run_service: RunService
    gateway: ActionExecutionGateway
    verifier: CapabilityVerifier

    def http_app(self):
        """Existing local HTTP contracts/UI; serving must remain inside open_runtime's context."""
        return create_app(
            self.planning,
            run_service=self.run_service,
            action_gateway=self.gateway,
            capability_verifier=self.verifier,
        )


@contextmanager
def open_runtime(
    *, gate_path, provider, capability, registry, verifier, policy, predecessor_stopped=False
):
    """Hold the existing single-owner fence through the entire consumer lifetime.

    No implicit registration, credentials, v1 migration or external effects on startup.
    A new private v2 gate is initialized by the existing repository implementation.
    """
    if (
        type(capability) is not ToolCapability
        or type(registry) is not ExecutableRegistry
        or registry.bindings != (capability.binding,)
        or type(verifier) is not CapabilityVerifier
        or verifier.binding != capability.binding
        or verifier._digest is None
        or policy is None
    ):
        raise ActionError(ActionCode.TOOL_NOT_BOUND)
    gate_path = resolve_database_path(gate_path)
    with single_owner(gate_path, predecessor_stopped=predecessor_stopped):
        validator = capability.build_validator()
        planning = PlanningService(provider, validator)
        runs = SQLiteV2RunRepository(gate_path, validator)
        actions = SQLiteExecutionRepository(runs, ActionCompiler(validator, registry.bindings))
        gateway = ActionExecutionGateway(actions, policy, registry)
        recover_runs(runs)
        recover_actions(actions, predecessor_stopped=predecessor_stopped)
        yield Runtime(planning, runs, actions, RunService(runs, planning), gateway, verifier)
