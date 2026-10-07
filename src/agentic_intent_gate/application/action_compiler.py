"""Pure trusted compilation. This registry contains identifiers, never executable callables."""

from dataclasses import dataclass

from agentic_intent_gate.domain.action import ActionCode, ActionError, CompiledSpec, identifier
from agentic_intent_gate.domain.approval import ApprovalChoice
from agentic_intent_gate.domain.approval_binding import digest, plan_digest
from agentic_intent_gate.domain.contracts import Risk
from agentic_intent_gate.domain.run import RunState
from agentic_intent_gate.domain.run_validation import canonical_json, valid_uuid

COMPILER_REVISION = "action-compiler-v1"


@dataclass(frozen=True)
class ToolBinding:
    tool_name: str
    tool_version: str
    adapter_id: str
    binding_revision: str
    target: str
    execution_scope: str

    def __post_init__(self):
        if (
            (self.tool_name, self.tool_version, self.target)
            != ("append_service_note", "1", "service-demo")
            or not identifier(self.adapter_id)
            or not identifier(self.binding_revision)
            or not valid_uuid(self.execution_scope)
        ):
            raise ActionError(ActionCode.TOOL_NOT_BOUND)


def specification_digest(facts, binding):
    return digest(
        {
            "identity_revision": "action-spec-v1",
            **facts,
            "approval_identity_revision": binding.identity_revision,
            "approval_plan_digest": binding.approval_plan_digest,
        }
    )


class ActionCompiler:
    def __init__(self, validator, bindings=()):
        self.validator = validator
        self.bindings = tuple(bindings)
        if any(not isinstance(b, ToolBinding) for b in self.bindings) or len(self.bindings) > 1:
            raise ActionError(ActionCode.TOOL_NOT_BOUND)

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
        if (
            len(plan.steps) != 1
            or plan.risk_class is not Risk.SIDE_EFFECT
            or plan.target != "service-demo"
            or (plan.steps[0].tool_name, plan.steps[0].tool_version) != ("append_service_note", "1")
        ):
            raise ActionError(ActionCode.PLAN_NOT_EXECUTABLE)
        selected = next(
            (
                b
                for b in self.bindings
                if (b.tool_name, b.tool_version, b.target)
                == ("append_service_note", "1", plan.target)
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
            "compiler_revision": COMPILER_REVISION,
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
