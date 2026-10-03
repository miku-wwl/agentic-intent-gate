"""Same-origin preview page and unchanged schema-validated planning API."""

import asyncio
from importlib.resources import files

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from agentic_intent_gate.application.generate_plan import REQUEST_TIMEOUT_SECONDS, PlanningService
from agentic_intent_gate.domain.contracts import ErrorCode, PlanningResult, ValidationIssue
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


def create_app(service: PlanningService) -> FastAPI:
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

    return app
