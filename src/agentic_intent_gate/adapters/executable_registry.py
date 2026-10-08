"""Static reviewed executable binding, separate from metadata. No plugin/import routing."""

from agentic_intent_gate.adapters.service_demo import DemoNoteExecutor
from agentic_intent_gate.domain.action import ActionCode, ActionError


class ExecutableRegistry:
    def __init__(self, executor=None):
        if executor is not None and type(executor) is not DemoNoteExecutor:
            raise ActionError(ActionCode.TOOL_NOT_BOUND)
        self._executor = executor
        self.bindings = (executor.binding,) if executor else ()

    def resolve(self, spec):
        if self._executor is None:
            raise ActionError(ActionCode.TOOL_NOT_BOUND)
        b = self._executor.binding
        if (
            spec.tool_name,
            spec.tool_version,
            spec.target,
            spec.adapter_id,
            spec.binding_revision,
            spec.execution_scope,
        ) != (
            b.tool_name,
            b.tool_version,
            b.target,
            b.adapter_id,
            b.binding_revision,
            b.execution_scope,
        ):
            raise ActionError(ActionCode.TOOL_NOT_BOUND)
        return self._executor
