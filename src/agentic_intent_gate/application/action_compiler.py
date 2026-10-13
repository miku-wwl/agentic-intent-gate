"""Pure trusted compilation. This registry contains identifiers, never executable callables."""

from dataclasses import dataclass, replace

from agentic_intent_gate.domain.action import ActionCode, ActionError, CompiledSpec, identifier
from agentic_intent_gate.domain.approval import ApprovalChoice
from agentic_intent_gate.domain.approval_binding import digest, plan_digest
from agentic_intent_gate.domain.contracts import Risk
from agentic_intent_gate.domain.execution_budget import (
    PROFILE_MARKER,
    binding_timeout,
    execution_timeout,
    profile_suffix,
)
from agentic_intent_gate.domain.run import RunState
from agentic_intent_gate.domain.run_validation import canonical_json, valid_uuid

COMPILER_REVISION = "action-compiler-v1"
EXTERNAL_COMPILER_REVISION = "action-compiler-v2"
BUDGET_COMPILER_REVISION = "action-compiler-v3"
LEGACY_TOOL = ("append_service_note", "1", "service-demo")


@dataclass(frozen=True)
class ToolBinding:
    tool_name: str
    tool_version: str
    adapter_id: str
    binding_revision: str
    target: str
    execution_scope: str

    def __post_init__(self):
        if not all(
            identifier(v)
            for v in (
                self.tool_name,
                self.tool_version,
                self.target,
                self.adapter_id,
                self.binding_revision,
            )
        ) or not valid_uuid(self.execution_scope):
            raise ActionError(ActionCode.TOOL_NOT_BOUND)

    @property
    def compiler_revision(self):
        return (
            COMPILER_REVISION
            if (self.tool_name, self.tool_version, self.target) == LEGACY_TOOL
            else EXTERNAL_COMPILER_REVISION
        )

    def matches(self, spec):
        return all(getattr(spec, key, None) == value for key, value in vars(self).items())


def execution_budget_binding(binding, execution_timeout_seconds):
    """Create an explicit extended profile from a freshly reviewed host ToolBinding.

    For example, revision 'audit-v2' at 15 seconds becomes 'audit-v2.exec-15s'.
    Retain the result across restarts; do not relabel existing Actions or effect stores.
    """
    if type(binding) is not ToolBinding:
        raise TypeError("A ToolBinding is required")
    value = execution_timeout(execution_timeout_seconds)
    if value <= 5:
        return binding
    if (binding.tool_name, binding.tool_version, binding.target) == LEGACY_TOOL:
        raise ValueError("Extended budgets require a non-demo consumer binding")
    if PROFILE_MARKER in binding.binding_revision:
        raise ValueError("Use a fresh reviewed binding revision without an execution suffix")
    return replace(binding, binding_revision=binding.binding_revision + profile_suffix(value))


def specification_digest(facts, binding):
    return digest(
        {
            "identity_revision": (
                "action-spec-v1"
                if facts["compiler_revision"] == COMPILER_REVISION
                else (
                    "action-spec-v3"
                    if facts["compiler_revision"] == BUDGET_COMPILER_REVISION
                    else "action-spec-v2"
                )
            ),
            **facts,
            "approval_identity_revision": binding.identity_revision,
            "approval_plan_digest": binding.approval_plan_digest,
        }
    )


@dataclass(frozen=True, init=False)
class ActionCompiler:
    validator: object
    bindings: tuple[ToolBinding, ...]
    execution_timeout_seconds: float

    def __init__(self, validator, bindings=(), *, execution_timeout_seconds=5):
        object.__setattr__(self, "validator", validator)
        object.__setattr__(self, "bindings", tuple(bindings))
        budget = execution_timeout(execution_timeout_seconds)
        object.__setattr__(self, "execution_timeout_seconds", budget)
        if any(not isinstance(b, ToolBinding) for b in self.bindings) or len(self.bindings) > 1:
            raise ActionError(ActionCode.TOOL_NOT_BOUND)
        if budget > 5 and (
            len(self.bindings) != 1
            or self.bindings[0].compiler_revision == COMPILER_REVISION
            or binding_timeout(self.bindings[0].binding_revision) != budget
        ):
            raise ValueError(
                "The extended execution budget must match its reviewed binding profile"
            )

    def compiler_revision_for(self, binding):
        return (
            BUDGET_COMPILER_REVISION
            if self.execution_timeout_seconds > 5
            else binding.compiler_revision
        )

    def _facts(self, run, decision, binding):
        if (
            run.state is not RunState.Approved
            or run.version != 5
            or run.plan is None
            or decision is None
            or decision.decision is not ApprovalChoice.APPROVE
            or decision.run_id != run.run_id
            or decision.created_at != run.updated_at
            or (decision.run_version_before, decision.run_version_after) != (4, 5)
        ):
            raise ActionError(ActionCode.RUN_NOT_ELIGIBLE)
        binding.verify(decision, run.plan, self.validator, admission=True)
        plan = self.validator.parse_plan(canonical_json(run.plan.to_dict()))
        if len(plan.steps) != 1 or plan.risk_class is not Risk.SIDE_EFFECT:
            raise ActionError(ActionCode.PLAN_NOT_EXECUTABLE)
        selected = next(
            (
                b
                for b in self.bindings
                if (b.tool_name, b.tool_version, b.target)
                == (plan.steps[0].tool_name, plan.steps[0].tool_version, plan.target)
            ),
            None,
        )
        if selected is None:
            raise ActionError(ActionCode.TOOL_NOT_BOUND)
        facts = {
            "run_id": run.run_id,
            "approved_run_version": 5,
            "approval_decision_id": decision.decision_id,
            "plan_digest": plan_digest(plan, self.validator),
            "step_index": 0,
            "tool_name": selected.tool_name,
            "tool_version": selected.tool_version,
            "target": plan.target,
            "normalized_arguments": dict(plan.steps[0].arguments),
            "risk_class": plan.risk_class.value,
            "compiler_revision": self.compiler_revision_for(selected),
            "adapter_id": selected.adapter_id,
            "binding_revision": selected.binding_revision,
            "execution_scope": selected.execution_scope,
        }
        return facts

    def compile(self, run, decision, binding):
        facts = self._facts(run, decision, binding)
        return CompiledSpec(
            **facts
            | {
                "risk_class": Risk(facts["risk_class"]),
                "spec_digest": specification_digest(facts, binding),
            }
        )

    def validate_spec(self, spec, run, decision, binding):
        """Verify retained facts/digest without creating a new compiled specification."""
        facts = self._facts(run, decision, binding)
        expected = facts | {"spec_digest": specification_digest(facts, binding)}
        if spec.to_dict() != expected:
            from agentic_intent_gate.domain.validation import fail

            fail("ACTION_SPEC_LINK")
