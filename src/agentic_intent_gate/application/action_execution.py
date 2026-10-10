"""Explicit verified commands. Only a confirmed non-replayed claim dispatches once."""

import asyncio
import math

from agentic_intent_gate.domain.action import ActionCode, ActionError, ActionState, safe_result
from agentic_intent_gate.domain.run_validation import valid_uuid
from agentic_intent_gate.ports.action_repository import CommandResult
from agentic_intent_gate.ports.authorization import DenyAuthorization
from agentic_intent_gate.ports.tool_executor import (
    EXTERNAL_PAYLOAD_REVISION,
    AttemptContext,
    DenyToolRegistry,
    ExecutionSpec,
    Outcome,
    ToolOutcome,
)


def classify(outcome, spec, effect_key):
    unknown = safe_result(ActionState.Unknown)
    if (
        not isinstance(outcome, ToolOutcome)
        or not isinstance(outcome.category, Outcome)
        or (outcome.effect_key, outcome.execution_scope, outcome.target, outcome.payload_digest)
        != (effect_key, spec.execution_scope, spec.target, spec.payload_digest())
    ):
        return unknown
    if (
        outcome.category is Outcome.SUCCESS
        and valid_uuid(outcome.receipt_reference)
        and outcome.no_effect_reason is None
    ):
        return safe_result(
            ActionState.Succeeded,
            outcome.receipt_reference,
            compiler_revision=(
                "action-compiler-v2"
                if spec.identity_revision == EXTERNAL_PAYLOAD_REVISION
                else "action-compiler-v1"
            ),
        )
    if (
        outcome.category is Outcome.CONFIRMED_FAILURE
        and outcome.receipt_reference is None
        and outcome.no_effect_reason
        in {"INPUT_REJECTED", "PAYLOAD_CONFLICT", "STORE_IDENTITY_MISMATCH"}
    ):
        return safe_result(ActionState.Failed)
    return unknown


class ActionExecutionGateway:
    def __init__(self, repository, policy=None, registry=None, *, tool_timeout_seconds=5):
        self.repository = repository
        self.policy = policy if policy is not None else DenyAuthorization()
        self.registry = registry if registry is not None else DenyToolRegistry()
        if (
            not math.isfinite(tool_timeout_seconds)
            or not 0 < tool_timeout_seconds <= 5
            or repository.compiler.bindings != self.registry.bindings
        ):
            raise ActionError(ActionCode.TOOL_NOT_BOUND)
        self.tool_timeout_seconds = tool_timeout_seconds

    def require(self, principal, operation):
        self.policy.require(principal, operation)

    def admit(self, command, principal):
        self.require(principal, "admit")
        return self.repository.admit(command, principal=principal, policy=self.policy)

    def get(self, action_id, principal):
        self.require(principal, "read")
        action = self.repository.get(action_id)
        if action is None:
            raise ActionError(ActionCode.NOT_FOUND)
        # Historical visibility uses trusted scope, not current executable binding.
        if action.spec.execution_scope != principal.execution_scope:
            raise ActionError(ActionCode.NOT_AUTHORIZED)
        return action

    def list_for_run(self, run_id, principal):
        self.require(principal, "read")
        actions = self.repository.list_for_run(run_id)
        if any(a.spec.execution_scope != principal.execution_scope for a in actions):
            raise ActionError(ActionCode.NOT_AUTHORIZED)
        return actions

    def authorize(self, command, principal):
        self.require(principal, "authorize")
        return self.repository.authorize(command, principal, self.policy)

    async def execute(self, command, principal, *, deadline=None):
        self.require(principal, "execute")
        claim = self.repository.claim(command, principal, self.policy)
        if claim.replayed:
            return CommandResult(claim.action, True)
        action, spec = claim.action, ExecutionSpec.from_compiled(claim.action.spec)
        tool_deadline = asyncio.get_running_loop().time() + self.tool_timeout_seconds
        context = AttemptContext(
            claim.attempt_id,
            min(tool_deadline, deadline) if deadline is not None else tool_deadline,
            asyncio.Event(),
        )
        result = safe_result(ActionState.Unknown)
        cancelled = False
        try:
            executor = self.registry.resolve(spec)
            async with asyncio.timeout_at(context.deadline):
                outcome = await executor.execute(spec, action.effect_key, context)
            result = classify(outcome, spec, action.effect_key)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            # A deadline or arbitrary adapter/storage fault is not authoritative no-effect proof.
            pass
        finally:
            context.cancelled.set()
        # No gate transaction was held while awaiting the adapter; no detach/retry/helper spawn.
        finished = self.repository.finish(action.action_id, claim.attempt_id, result)
        if cancelled:
            raise asyncio.CancelledError
        return CommandResult(finished, False)
