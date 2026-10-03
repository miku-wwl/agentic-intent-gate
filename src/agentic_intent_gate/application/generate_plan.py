"""Vendor-independent planning service with one provider attempt."""

import asyncio

from agentic_intent_gate.domain.contracts import ErrorCode, Intent, PlanningResult, PlanningState
from agentic_intent_gate.domain.validation import PlanValidator, ValidationFailure
from agentic_intent_gate.ports.model_provider import ModelProvider, ModelProviderError

REQUEST_TIMEOUT_SECONDS = 30


class PlanningService:
    def __init__(
        self,
        provider: ModelProvider,
        validator: PlanValidator,
        *,
        model_timeout_seconds: float = 28,
    ) -> None:
        self.provider = provider
        self.validator = validator
        self.model_timeout_seconds = model_timeout_seconds

    async def generate_plan(
        self, intent: Intent, *, deadline: float | None = None
    ) -> PlanningResult:
        states = []
        try:
            self.validator.validate_intent(intent)
            states.append(PlanningState.IntentReceived)
            loop = asyncio.get_running_loop()
            deadline = deadline if deadline is not None else loop.time() + REQUEST_TIMEOUT_SECONDS
            async with asyncio.timeout_at(deadline):
                states.append(PlanningState.PlanGenerating)
                budget = min(self.model_timeout_seconds, deadline - loop.time() - 2)
                if budget <= 0:
                    raise TimeoutError
                async with asyncio.timeout(budget):
                    raw = await self.provider.generate_plan(
                        intent,
                        tools=self.validator.tools,
                        plan_schema=self.validator.plan_schema,
                        timeout_seconds=budget,
                    )
                states.append(PlanningState.PlanGenerated)
                plan = self.validator.parse_plan(raw)
                if loop.time() >= deadline:
                    raise TimeoutError
                states.append(PlanningState.PlanValidated)
                return PlanningResult(PlanningState.PlanValidated, plan, None, tuple(states))
        except ValidationFailure as exc:
            accepted = bool(states)
            return PlanningResult.reject(
                ErrorCode.PLAN_VALIDATION_ERROR if accepted else ErrorCode.INVALID_INTENT,
                issues=exc.issues,
                states=tuple(states),
                accepted=accepted,
            )
        except ModelProviderError as exc:
            return PlanningResult.reject(exc.code, states=tuple(states))
        except TimeoutError:
            return PlanningResult.reject(ErrorCode.MODEL_TIMEOUT, states=tuple(states))
        except Exception:
            return PlanningResult.reject(ErrorCode.INTERNAL_ERROR, states=tuple(states))
