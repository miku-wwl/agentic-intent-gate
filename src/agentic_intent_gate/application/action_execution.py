"""Explicit verified commands. Only a confirmed non-replayed claim dispatches once."""

import asyncio
import math
from dataclasses import dataclass

from agentic_intent_gate.domain.action import ActionCode, ActionError, ActionState, safe_result
from agentic_intent_gate.domain.execution_budget import execution_timeout
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


@dataclass(frozen=True, init=False)
class ActionExecutionGateway:
    repository: object
    policy: object
    registry: object
    tool_timeout_seconds: float

    def __init__(self, repository, policy=None, registry=None, *, tool_timeout_seconds=5):
        budget = execution_timeout(tool_timeout_seconds)
        object.__setattr__(self, "repository", repository)
        object.__setattr__(self, "policy", policy if policy is not None else DenyAuthorization())
        object.__setattr__(
            self, "registry", registry if registry is not None else DenyToolRegistry()
        )
        if (
            repository.compiler.bindings != self.registry.bindings
            or (budget > 5 and budget != repository.compiler.execution_timeout_seconds)
            or (budget <= 5 and repository.compiler.execution_timeout_seconds > 5)
        ):
            raise ActionError(ActionCode.TOOL_NOT_BOUND)
        object.__setattr__(self, "tool_timeout_seconds", budget)

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
        if deadline is not None:
            try:
                valid_deadline = type(deadline) in (int, float) and math.isfinite(deadline)
            except OverflowError:
                valid_deadline = False
            if not valid_deadline:
                raise ActionError(ActionCode.INVALID_REQUEST)
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
                # timeout_at cannot preempt synchronous work or an adapter swallowing cancellation.
                if not context.expired():
                    outcome = await executor.execute(spec, action.effect_key, context)
                    if not context.expired() and not asyncio.current_task().cancelling():
                        result = classify(outcome, spec, action.effect_key)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            # A deadline or arbitrary adapter/storage fault is not authoritative no-effect proof.
            pass
        finally:
            cancelled = cancelled or bool(asyncio.current_task().cancelling())
            context.cancelled.set()
        # No gate transaction was held while awaiting the adapter; no detach/retry/helper spawn.
        finished = self.repository.finish(action.action_id, claim.attempt_id, result)
        if cancelled:
            raise asyncio.CancelledError
        return CommandResult(finished, False)
