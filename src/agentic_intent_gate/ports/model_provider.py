"""ModelProvider returns text, never a trusted Plan."""

from collections.abc import Mapping, Sequence
from typing import Protocol

from agentic_intent_gate.domain.contracts import ERROR_MESSAGES, ErrorCode, Intent, Tool


class ModelProviderError(Exception):
    def __init__(self, code: ErrorCode):
        if code not in {
            ErrorCode.MODEL_TIMEOUT,
            ErrorCode.MODEL_UNAVAILABLE,
            ErrorCode.MODEL_RESPONSE_ERROR,
        }:
            raise ValueError("Invalid model error classification")
        self.code = code
        super().__init__(ERROR_MESSAGES[code])


class ModelProvider(Protocol):
    async def generate_plan(
        self,
        intent: Intent,
        *,
        tools: Sequence[Tool],
        plan_schema: Mapping[str, object],
        timeout_seconds: float,
    ) -> str: ...
