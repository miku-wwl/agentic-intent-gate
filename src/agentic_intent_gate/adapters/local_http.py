"""Same-origin preview page and unchanged schema-validated planning API."""

import asyncio
from importlib.resources import files

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from agentic_intent_gate.application.generate_plan import REQUEST_TIMEOUT_SECONDS, PlanningService
from agentic_intent_gate.application.runs import RunService
from agentic_intent_gate.domain.approval import ApprovalDecisionRequest
from agentic_intent_gate.domain.contracts import (
    HTTP_STATUSES,
    ErrorCode,
    PlanningResult,
    ValidationIssue,
)
from agentic_intent_gate.domain.run import RUN_MESSAGES, RunErrorCode, RunStoreError
from agentic_intent_gate.domain.run_validation import valid_uuid
from agentic_intent_gate.domain.validation import REQUEST_BYTES, ValidationFailure


def _json_utf8(content_type: str) -> bool:
    parts = content_type.lower().split(";")
    if parts[0].strip() != "application/json":
        return False
    charset_seen = False
    for part in parts[1:]:
        name, separator, value = part.strip().partition("=")
        if name.strip() == "charset":
            if charset_seen or not separator or value.strip().strip('"') not in {"utf-8", "utf8"}:
                return False
            charset_seen = True
    return True


def create_app(
    service: PlanningService,
    *,
    run_service: RunService | None = None,
    action_gateway=None,
    capability_verifier=None,
) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, debug=False)
    page = files("agentic_intent_gate.adapters").joinpath("static", "index.html").read_text("utf-8")

    @app.get("/", response_class=HTMLResponse)
    async def planning_preview() -> HTMLResponse:
        return HTMLResponse(page)

    @app.post("/api/plans")
    async def generate_plan(request: Request) -> JSONResponse:
        deadline = asyncio.get_running_loop().time() + REQUEST_TIMEOUT_SECONDS
        accepted = False
        try:
            if not _json_utf8(request.headers.get("content-type", "")):
                result = PlanningResult.reject(ErrorCode.UNSUPPORTED_MEDIA_TYPE, accepted=False)
            else:
                body = bytearray()
                async with asyncio.timeout_at(deadline):
                    async for chunk in request.stream():
                        if len(body) + len(chunk) > REQUEST_BYTES:
                            result = PlanningResult.reject(
                                ErrorCode.REQUEST_TOO_LARGE, accepted=False
                            )
                            break
                        body.extend(chunk)
                    else:
                        intent = service.validator.parse_intent(bytes(body))
                        accepted = True
                        result = await service.generate_plan(intent, deadline=deadline)
        except ValidationFailure as exc:
            result = PlanningResult.reject(
                ErrorCode.INVALID_INTENT, issues=exc.issues, accepted=False
            )
        except TimeoutError:
            result = PlanningResult.reject(
                ErrorCode.MODEL_TIMEOUT if accepted else ErrorCode.INVALID_INTENT,
                issues=() if accepted else (ValidationIssue("/", "REQUEST_TIMEOUT"),),
                accepted=accepted,
            )
        except Exception:
            result = PlanningResult.reject(ErrorCode.INTERNAL_ERROR, accepted=accepted)
        return JSONResponse(result.to_dict(), status_code=result.http_status)

    if run_service is not None:
        _add_run_routes(app, run_service)
    if action_gateway is not None:
        from agentic_intent_gate.adapters.action_http import add_action_routes

        add_action_routes(app, action_gateway, capability_verifier)
    return app


RUN_STATUSES = {
    **HTTP_STATUSES,
    RunErrorCode.INVALID_APPROVAL: 400,
    RunErrorCode.RUN_NOT_FOUND: 404,
    RunErrorCode.STALE_RUN_VERSION: 409,
    RunErrorCode.INVALID_RUN_TRANSITION: 409,
    RunErrorCode.DATABASE_UNAVAILABLE: 503,
}


def _run_failure(exc: RunStoreError) -> JSONResponse:
    code = exc.code
    # Schema failures are startup-only; a later storage change is operational unavailability.
    if code == RunErrorCode.DATABASE_SCHEMA_UNSUPPORTED:
        code = RunErrorCode.DATABASE_UNAVAILABLE
    return JSONResponse(
        {"run_id": exc.run_id, "error": {"code": code.value, "message": RUN_MESSAGES[code]}},
        status_code=RUN_STATUSES[code],
    )


async def _run_body(
    request: Request, deadline: float, invalid_code: ErrorCode | RunErrorCode
) -> bytes:
    if not _json_utf8(request.headers.get("content-type", "")):
        raise RunStoreError(ErrorCode.UNSUPPORTED_MEDIA_TYPE)
    body = bytearray()
    try:
        async with asyncio.timeout_at(deadline):
            async for chunk in request.stream():
                if len(body) + len(chunk) > REQUEST_BYTES:
                    raise RunStoreError(ErrorCode.REQUEST_TOO_LARGE)
                body.extend(chunk)
    except TimeoutError:
        raise RunStoreError(invalid_code) from None
    return bytes(body)


def _path_run_id(run_id: str) -> None:
    if not valid_uuid(run_id):
        raise RunStoreError(RunErrorCode.RUN_NOT_FOUND)


def _add_run_routes(app: FastAPI, service: RunService) -> None:
    @app.post("/api/runs")
    async def create_run(request: Request) -> JSONResponse:
        deadline = asyncio.get_running_loop().time() + REQUEST_TIMEOUT_SECONDS
        try:
            body = await _run_body(request, deadline, ErrorCode.INVALID_INTENT)
            intent = service.validator.parse_intent(body)
            run = await service.create(intent, deadline=deadline)
            return JSONResponse(
                run.to_dict(), status_code=201, headers={"Location": f"/api/runs/{run.run_id}"}
            )
        except ValidationFailure:
            return _run_failure(RunStoreError(ErrorCode.INVALID_INTENT))
        except RunStoreError as exc:
            return _run_failure(exc)
        except Exception:
            return _run_failure(RunStoreError(ErrorCode.INTERNAL_ERROR))

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str) -> JSONResponse:
        try:
            _path_run_id(run_id)
            return JSONResponse(service.get(run_id).to_dict())
        except RunStoreError as exc:
            return _run_failure(exc)
        except Exception:
            return _run_failure(RunStoreError(ErrorCode.INTERNAL_ERROR, run_id))

    @app.post("/api/runs/{run_id}/approval")
    async def decide_run(run_id: str, request: Request) -> JSONResponse:
        deadline = asyncio.get_running_loop().time() + REQUEST_TIMEOUT_SECONDS
        try:
            _path_run_id(run_id)
            try:
                body = await _run_body(request, deadline, RunErrorCode.INVALID_APPROVAL)
            except RunStoreError as exc:
                raise RunStoreError(exc.code, run_id) from None
            decision = ApprovalDecisionRequest.parse(body)
            return JSONResponse(service.decide(run_id, decision).to_dict())
        except ValidationFailure:
            return _run_failure(RunStoreError(RunErrorCode.INVALID_APPROVAL, run_id))
        except RunStoreError as exc:
            return _run_failure(exc)
        except Exception:
            return _run_failure(RunStoreError(ErrorCode.INTERNAL_ERROR, run_id))
