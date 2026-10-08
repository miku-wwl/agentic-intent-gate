"""Five frozen capability-protected APIs; no cookie/query/body authority or UI changes."""

import asyncio
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse

from agentic_intent_gate.adapters.configuration import loopback_host
from agentic_intent_gate.adapters.local_capability import CapabilityVerifier
from agentic_intent_gate.domain.action import ActionCode, ActionError, ActionState
from agentic_intent_gate.domain.approval_binding import BindingError
from agentic_intent_gate.domain.contracts import HTTP_STATUSES
from agentic_intent_gate.domain.run import RUN_MESSAGES, RunErrorCode, RunStoreError
from agentic_intent_gate.domain.run_validation import valid_uuid
from agentic_intent_gate.domain.validation import ValidationFailure
from agentic_intent_gate.ports.action_repository import ActionCommand, AdmissionCommand

STATUSES = {
    ActionCode.AUTHENTICATION_REQUIRED: 401,
    ActionCode.NOT_AUTHORIZED: 403,
    ActionCode.INVALID_REQUEST: 400,
    ActionCode.KEY_REQUIRED: 428,
    ActionCode.NOT_FOUND: 404,
    ActionCode.PLAN_NOT_EXECUTABLE: 422,
    ActionCode.TOOL_REJECTED: 422,
    ActionCode.DATABASE_UNAVAILABLE: 503,
    ActionCode.INTERNAL_ERROR: 500,
}


def add_action_routes(app, gateway, verifier=None):
    verifier = verifier if verifier is not None else CapabilityVerifier()

    async def handle(request, resource_id, operation, *, run_resource=False):
        deadline = asyncio.get_running_loop().time() + 10
        run_id = resource_id if run_resource and valid_uuid(resource_id) else None
        action_id = resource_id if not run_resource and valid_uuid(resource_id) else None
        try:
            headers = request.headers.getlist("authorization")
            principal = verifier.verify(headers[0] if len(headers) == 1 else None)
            permission = "read" if operation in {"get", "list"} else operation
            gateway.require(principal, permission)
            origin = request.headers.get("origin")
            if origin is not None:
                try:
                    parsed, actual = urlsplit(origin), urlsplit(str(request.url))
                    if (
                        not loopback_host(parsed.hostname or "")
                        or parsed.scheme != "http"
                        or parsed.username is not None
                        or parsed.password is not None
                        or parsed.path
                        or parsed.query
                        or parsed.fragment
                        or (parsed.scheme, parsed.hostname, parsed.port or 80)
                        != (actual.scheme, actual.hostname, actual.port or 80)
                    ):
                        raise ValueError
                except ValueError:
                    raise ActionError(ActionCode.NOT_AUTHORIZED) from None
            if not valid_uuid(resource_id) or request.url.query:
                raise ActionError(ActionCode.INVALID_REQUEST)
            if operation == "get":
                return JSONResponse(gateway.get(resource_id, principal).snapshot())
            if operation == "list":
                actions = gateway.list_for_run(resource_id, principal)
                return JSONResponse(
                    {"run_id": resource_id, "actions": [a.snapshot() for a in actions]}
                )
            keys = request.headers.getlist("idempotency-key")
            if not keys:
                raise ActionError(ActionCode.KEY_REQUIRED)
            if len(keys) != 1 or not valid_uuid(keys[0]):
                raise ActionError(ActionCode.INVALID_REQUEST)
            from agentic_intent_gate.adapters.local_http import _run_body

            body = await _run_body(request, deadline, RunErrorCode.INVALID_APPROVAL)
            if operation == "admit":
                command = AdmissionCommand.parse(
                    resource_id, body, subject_id=principal.subject_id, idempotency_key=keys[0]
                )
                result = gateway.admit(command, principal)
                run_id = result.action.spec.run_id
                return JSONResponse(result.to_dict(), status_code=201 if result.created else 200)
            command = ActionCommand.parse(resource_id, body, idempotency_key=keys[0])
            result = (
                gateway.authorize(command, principal)
                if operation == "authorize"
                else await gateway.execute(command, principal, deadline=deadline)
            )
            run_id = result.action.spec.run_id
            status = (
                202
                if operation == "execute" and result.action.state is ActionState.Executing
                else 200
            )
            return JSONResponse(result.to_dict(), status_code=status)
        except ValidationFailure:
            exc = ActionError(ActionCode.INVALID_REQUEST)
            status = 400
        except ActionError as error:
            exc, status = error, STATUSES.get(error.code, 409)
        except BindingError as error:
            exc, status = error, 409
        except RunStoreError as error:
            code = error.code
            if code in {
                RunErrorCode.DATABASE_SCHEMA_UNSUPPORTED,
                RunErrorCode.DATABASE_UNAVAILABLE,
            }:
                exc, status = ActionError(ActionCode.DATABASE_UNAVAILABLE), 503
            elif code == RunErrorCode.INVALID_APPROVAL:
                exc, status = ActionError(ActionCode.INVALID_REQUEST), 400
            else:
                message = RUN_MESSAGES[code]
                status = {
                    RunErrorCode.RUN_NOT_FOUND: 404,
                    RunErrorCode.STALE_RUN_VERSION: 409,
                    RunErrorCode.DATABASE_UNAVAILABLE: 503,
                }.get(code, HTTP_STATUSES.get(code, 500))
                return JSONResponse(
                    {
                        "run_id": run_id,
                        "action_id": action_id,
                        "error": {"code": code.value, "message": message},
                    },
                    status_code=status,
                )
        except Exception:
            exc, status = ActionError(ActionCode.INTERNAL_ERROR), 500
        return JSONResponse(
            {
                "run_id": run_id,
                "action_id": action_id,
                "error": {"code": exc.code.value, "message": str(exc)},
            },
            status_code=status,
        )

    @app.post("/api/runs/{run_id}/actions")
    async def admit(run_id: str, request: Request):
        return await handle(request, run_id, "admit", run_resource=True)

    @app.get("/api/runs/{run_id}/actions")
    async def list_actions(run_id: str, request: Request):
        return await handle(request, run_id, "list", run_resource=True)

    @app.get("/api/actions/{action_id}")
    async def get_action(action_id: str, request: Request):
        return await handle(request, action_id, "get")

    @app.post("/api/actions/{action_id}/authorize")
    async def authorize(action_id: str, request: Request):
        return await handle(request, action_id, "authorize")

    @app.post("/api/actions/{action_id}/execute")
    async def execute(action_id: str, request: Request):
        return await handle(request, action_id, "execute")
