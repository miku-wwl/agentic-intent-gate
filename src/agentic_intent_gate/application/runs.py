"""Request-owned single-attempt planning over durable, guarded Run barriers."""

import asyncio
from uuid import uuid4

from agentic_intent_gate.application.generate_plan import REQUEST_TIMEOUT_SECONDS, PlanningService
from agentic_intent_gate.domain.approval import ApprovalDecisionRequest
from agentic_intent_gate.domain.contracts import ErrorCode, Intent, PlanningState
from agentic_intent_gate.domain.run import (
    PLANNING_ERRORS,
    Run,
    RunErrorCode,
    RunState,
    RunStoreError,
)
from agentic_intent_gate.domain.run_validation import canonical_json, utc_now
from agentic_intent_gate.domain.validation import ValidationFailure
from agentic_intent_gate.ports.run_repository import RunRepository


class RunService:
    def __init__(self, repository: RunRepository, planning_service: PlanningService) -> None:
        self.repository = repository
        self.planning_service = planning_service
        self.validator = planning_service.validator

    async def create(self, intent: Intent, *, deadline: float | None = None) -> Run:
        self.validator.validate_intent(intent)
        loop = asyncio.get_running_loop()
        deadline = deadline if deadline is not None else loop.time() + REQUEST_TIMEOUT_SECONDS
        run_id = str(uuid4())
        try:
            run = self.repository.create(run_id, intent, utc_now())
            await asyncio.sleep(0)  # Observe cancellation before the start barrier/model call.
            if deadline - loop.time() - 2 <= 0:
                return self.repository.reject_planning(run_id, run.version, ErrorCode.MODEL_TIMEOUT)
            run = self.repository.start_generation(run_id, run.version)
            await asyncio.sleep(0)
            try:
                result = await self.planning_service.generate_plan(intent, deadline=deadline)
            except Exception:
                return self.repository.reject_planning(
                    run_id, run.version, ErrorCode.INTERNAL_ERROR
                )
            if result.error is not None:
                code = result.error.code
                if not isinstance(code, ErrorCode) or code not in PLANNING_ERRORS:
                    code = ErrorCode.INTERNAL_ERROR
                return self.repository.reject_planning(run_id, run.version, code)
            if result.state != PlanningState.PlanValidated or result.plan is None:
                return self.repository.reject_planning(
                    run_id, run.version, ErrorCode.INTERNAL_ERROR
                )
            try:
                plan = self.validator.parse_plan(canonical_json(result.plan.to_dict()))
            except ValidationFailure:
                return self.repository.reject_planning(
                    run_id, run.version, ErrorCode.PLAN_VALIDATION_ERROR
                )
            run = self.repository.save_validated_plan(run_id, run.version, plan)
            await asyncio.sleep(0)  # A committed Plan survives cancellation before routing.
            return self.repository.route_validated_plan(run_id, run.version)
        except asyncio.CancelledError:
            self._record_cancellation(run_id)
            raise
        except RunStoreError as exc:
            raise RunStoreError(exc.code, run_id) from None
        except Exception:
            raise RunStoreError(ErrorCode.INTERNAL_ERROR, run_id) from None

    def _record_cancellation(self, run_id: str) -> None:
        try:
            run = self.repository.get(run_id)
            if run is not None and run.state in {RunState.IntentReceived, RunState.PlanGenerating}:
                self.repository.reject_planning(run_id, run.version, ErrorCode.INTERNAL_ERROR)
        except Exception:
            # One guarded attempt only; an intermediate Run remains eligible at next startup.
            pass

    def get(self, run_id: str) -> Run:
        run = self.repository.get(run_id)
        if run is None:
            raise RunStoreError(RunErrorCode.RUN_NOT_FOUND, run_id)
        return run

    def decide(self, run_id: str, request: ApprovalDecisionRequest) -> Run:
        return self.repository.decide_approval(run_id, request)
