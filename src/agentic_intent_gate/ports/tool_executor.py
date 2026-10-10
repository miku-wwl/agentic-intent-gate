"""Narrow immutable effect data and bounded context, never control-plane DB/credentials."""

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from agentic_intent_gate.domain.action import ActionCode, ActionError, CompiledSpec
from agentic_intent_gate.domain.approval_binding import digest
from agentic_intent_gate.domain.contracts import freeze_json, thaw_json

LEGACY_PAYLOAD_REVISION = "demo-note-payload-v1"
EXTERNAL_PAYLOAD_REVISION = "tool-payload-v1"


@dataclass(frozen=True)
class ExecutionSpec:
    tool_name: str
    tool_version: str
    target: str
    arguments: Mapping
    adapter_id: str
    binding_revision: str
    execution_scope: str
    identity_revision: str = LEGACY_PAYLOAD_REVISION

    def __post_init__(self):
        if self.identity_revision not in {LEGACY_PAYLOAD_REVISION, EXTERNAL_PAYLOAD_REVISION}:
            raise ActionError(ActionCode.TOOL_NOT_BOUND)
        object.__setattr__(self, "arguments", freeze_json(thaw_json(self.arguments)))

    @classmethod
    def from_compiled(cls, spec: CompiledSpec):
        from agentic_intent_gate.application.action_compiler import (
            COMPILER_REVISION,
            EXTERNAL_COMPILER_REVISION,
        )

        if spec.compiler_revision not in {COMPILER_REVISION, EXTERNAL_COMPILER_REVISION}:
            raise ActionError(ActionCode.TOOL_NOT_BOUND)
        return cls(
            spec.tool_name,
            spec.tool_version,
            spec.target,
            spec.normalized_arguments,
            spec.adapter_id,
            spec.binding_revision,
            spec.execution_scope,
            (
                LEGACY_PAYLOAD_REVISION
                if spec.compiler_revision == COMPILER_REVISION
                else EXTERNAL_PAYLOAD_REVISION
            ),
        )

    def payload_digest(self):
        return digest(
            {
                "identity_revision": self.identity_revision,
                "tool_name": self.tool_name,
                "tool_version": self.tool_version,
                "target": self.target,
                "execution_scope": self.execution_scope,
                "arguments": thaw_json(self.arguments),
                **(
                    {"adapter_id": self.adapter_id, "binding_revision": self.binding_revision}
                    if self.identity_revision == EXTERNAL_PAYLOAD_REVISION
                    else {}
                ),
            }
        )


@dataclass(frozen=True)
class AttemptContext:
    attempt_id: str
    deadline: float
    cancelled: asyncio.Event

    def expired(self):
        return self.cancelled.is_set() or asyncio.get_running_loop().time() >= self.deadline


class Outcome(StrEnum):
    SUCCESS = "SUCCESS"
    CONFIRMED_FAILURE = "CONFIRMED_FAILURE"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"


@dataclass(frozen=True)
class ToolOutcome:
    category: Outcome
    effect_key: str | None = None
    execution_scope: str | None = None
    target: str | None = None
    payload_digest: str | None = None
    receipt_reference: str | None = None
    no_effect_reason: str | None = None


class ToolExecutor(Protocol):
    async def execute(
        self, spec: ExecutionSpec, effect_key: str, context: AttemptContext
    ) -> ToolOutcome: ...


class ToolRegistry(Protocol):
    bindings: tuple

    def resolve(self, spec: ExecutionSpec) -> ToolExecutor: ...


class DenyToolRegistry:
    bindings = ()

    def resolve(self, spec):
        raise ActionError(ActionCode.TOOL_NOT_BOUND)
