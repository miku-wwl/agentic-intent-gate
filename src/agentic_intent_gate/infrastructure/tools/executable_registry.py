"""Static reviewed executable binding, separate from metadata. No plugin/import routing."""

from dataclasses import dataclass

from agentic_intent_gate.application.action_compiler import LEGACY_TOOL, ToolBinding
from agentic_intent_gate.domain.action import ActionCode, ActionError
from agentic_intent_gate.infrastructure.tools.service_demo import DemoNoteExecutor
from agentic_intent_gate.ports.tool_executor import (
    EXTERNAL_PAYLOAD_REVISION,
    LEGACY_PAYLOAD_REVISION,
)


@dataclass(frozen=True, init=False)
class ExecutableRegistry:
    """One startup-only host registration, never an HTTP or model-controlled registry."""

    _executor: object
    bindings: tuple[ToolBinding, ...]

    def __init__(self, executor=None, *, binding=None):
        if binding is None and executor is not None:
            if type(executor) is not DemoNoteExecutor:
                raise ActionError(ActionCode.TOOL_NOT_BOUND)
            binding = executor.binding
        if (binding is None) != (executor is None) or (
            binding is not None
            and (
                type(binding) is not ToolBinding
                or not callable(getattr(executor, "execute", None))
                or (
                    (binding.tool_name, binding.tool_version, binding.target) == LEGACY_TOOL
                    and type(executor) is not DemoNoteExecutor
                )
            )
        ):
            raise ActionError(ActionCode.TOOL_NOT_BOUND)
        object.__setattr__(self, "_executor", executor)
        object.__setattr__(self, "bindings", (binding,) if binding else ())

    def resolve(self, spec):
        if self._executor is None:
            raise ActionError(ActionCode.TOOL_NOT_BOUND)
        b = self.bindings[0]
        revision = (
            LEGACY_PAYLOAD_REVISION
            if (b.tool_name, b.tool_version, b.target) == LEGACY_TOOL
            else EXTERNAL_PAYLOAD_REVISION
        )
        if not b.matches(spec) or spec.identity_revision != revision:
            raise ActionError(ActionCode.TOOL_NOT_BOUND)
        return self._executor
