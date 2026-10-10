"""Direct contract-level dummy executor; deliberately absent from production composition."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from agentic_intent_gate.domain.approval_binding import digest
from agentic_intent_gate.domain.contracts import thaw_json
from agentic_intent_gate.domain.run_validation import canonical_json, valid_uuid
from agentic_intent_gate.ports.tool_executor import (
    AttemptContext,
    ExecutionSpec,
    Outcome,
    ToolOutcome,
)

TOOL_NAME = "dummy_record"
TOOL_VERSION = "1"
TARGET = "test-local-dummy"
ADAPTER_ID = "day4-dummy"
BINDING_REVISION = "dummy-v1"
PAYLOAD_IDENTITY_REVISION = "dummy-record-v1"
MAX_RECORD_BYTES = 256
MAX_ARGUMENTS_BYTES = 1024


class StoreApplyStatus(StrEnum):
    CREATED = "CREATED"
    REPLAYED = "REPLAYED"
    CONFLICT = "CONFLICT"
    REJECTED = "REJECTED"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"


@dataclass(frozen=True)
class StoreApplyResult:
    status: StoreApplyStatus
    receipt_reference: str | None = None


class DummyReceiptStore(Protocol):
    """Trusted injected boundary; the concrete test store is owned by Prompt 3."""

    def apply_once(
        self,
        effect_key: str,
        adapter_payload_digest: str,
        execution_scope: str,
        record: str,
    ) -> StoreApplyResult: ...


def _adapter_payload_digest(spec: ExecutionSpec, record: str) -> str:
    """Bind local receipt identity to the complete adapter binding and validated input."""
    return digest(
        {
            "identity_revision": PAYLOAD_IDENTITY_REVISION,
            "tool_name": spec.tool_name,
            "tool_version": spec.tool_version,
            "target": spec.target,
            "adapter_id": spec.adapter_id,
            "binding_revision": spec.binding_revision,
            "execution_scope": spec.execution_scope,
            "arguments": {"record": record},
        }
    )


class DeterministicDummyExecutor:
    """A deterministic ToolExecutor that only delegates one bounded record to its store."""

    def __init__(self, execution_scope: str, receipt_store: DummyReceiptStore) -> None:
        if not valid_uuid(execution_scope):
            raise ValueError("execution_scope must be a canonical UUIDv4")
        if not callable(getattr(receipt_store, "apply_once", None)):
            raise TypeError("receipt_store must provide apply_once")
        self._execution_scope = execution_scope
        self._receipt_store = receipt_store

    def _binding_matches(self, spec: ExecutionSpec) -> bool:
        return (
            spec.tool_name,
            spec.tool_version,
            spec.target,
            spec.adapter_id,
            spec.binding_revision,
            spec.execution_scope,
        ) == (
            TOOL_NAME,
            TOOL_VERSION,
            TARGET,
            ADAPTER_ID,
            BINDING_REVISION,
            self._execution_scope,
        )

    @staticmethod
    def _unknown() -> ToolOutcome:
        return ToolOutcome(Outcome.UNKNOWN_OUTCOME)

    @staticmethod
    def _evidence(
        spec: ExecutionSpec,
        effect_key: str,
        frozen_payload_digest: str,
        category: Outcome,
        *,
        receipt_reference: str | None = None,
        no_effect_reason: str | None = None,
    ) -> ToolOutcome:
        return ToolOutcome(
            category=category,
            effect_key=effect_key,
            execution_scope=spec.execution_scope,
            target=spec.target,
            payload_digest=frozen_payload_digest,
            receipt_reference=receipt_reference,
            no_effect_reason=no_effect_reason,
        )

    async def execute(
        self, spec: ExecutionSpec, effect_key: str, context: AttemptContext
    ) -> ToolOutcome:
        if not isinstance(spec, ExecutionSpec) or not isinstance(context, AttemptContext):
            return self._unknown()
        if not isinstance(effect_key, str):
            return self._unknown()

        try:
            frozen_payload_digest = spec.payload_digest()
        except Exception:
            # Without the frozen correlation digest, no outcome envelope is authoritative.
            return self._unknown()

        def rejected() -> ToolOutcome:
            return self._evidence(
                spec,
                effect_key,
                frozen_payload_digest,
                Outcome.CONFIRMED_FAILURE,
                no_effect_reason="INPUT_REJECTED",
            )

        if not self._binding_matches(spec):
            return rejected()

        try:
            arguments = thaw_json(spec.arguments)
            if not isinstance(arguments, Mapping) or set(arguments) != {"record"}:
                return rejected()
            record = arguments["record"]
            if type(record) is not str or not record:
                return rejected()
            record_bytes = record.encode("utf-8")
            canonical_arguments = canonical_json(arguments).encode("utf-8")
            if (
                len(record_bytes) > MAX_RECORD_BYTES
                or len(canonical_arguments) > MAX_ARGUMENTS_BYTES
            ):
                return rejected()
            adapter_digest = _adapter_payload_digest(spec, record)
        except (TypeError, ValueError, UnicodeError, RecursionError):
            return rejected()

        if not valid_uuid(effect_key) or not valid_uuid(context.attempt_id):
            return rejected()
        if context.expired():
            return self._unknown()

        try:
            result = self._receipt_store.apply_once(
                effect_key,
                adapter_digest,
                self._execution_scope,
                record,
            )
        except Exception:
            # An exception does not prove that a synchronous store did not commit.
            return self._unknown()

        if context.expired():
            return self._unknown()
        if type(result) is not StoreApplyResult or type(result.status) is not StoreApplyStatus:
            return self._unknown()

        if result.status in {StoreApplyStatus.CREATED, StoreApplyStatus.REPLAYED}:
            if type(result.receipt_reference) is not str or not valid_uuid(
                result.receipt_reference
            ):
                return self._unknown()
            return self._evidence(
                spec,
                effect_key,
                frozen_payload_digest,
                Outcome.SUCCESS,
                receipt_reference=result.receipt_reference,
            )

        if result.receipt_reference is not None:
            return self._unknown()

        no_effect_reasons = {
            StoreApplyStatus.CONFLICT: "PAYLOAD_CONFLICT",
            StoreApplyStatus.REJECTED: "INPUT_REJECTED",
            StoreApplyStatus.IDENTITY_MISMATCH: "STORE_IDENTITY_MISMATCH",
        }
        reason = no_effect_reasons.get(result.status)
        if reason is None:
            return self._unknown()
        return self._evidence(
            spec,
            effect_key,
            frozen_payload_digest,
            Outcome.CONFIRMED_FAILURE,
            no_effect_reason=reason,
        )
