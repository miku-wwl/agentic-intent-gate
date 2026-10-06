"""Exact Day 2 HTTP contracts, precedence and unchanged Day 1 factory behavior."""

import hashlib
import json
from uuid import uuid4

import httpx
import pytest
from run_helpers import advance, database_rows, plan_for

from agentic_intent_gate.adapters import local_http
from agentic_intent_gate.adapters.local_http import create_app
from agentic_intent_gate.application.generate_plan import PlanningService
from agentic_intent_gate.application.runs import RunService
from agentic_intent_gate.domain.contracts import ErrorCode, Risk
from agentic_intent_gate.domain.run import RUN_MESSAGES, RunErrorCode, RunState, RunStoreError
from agentic_intent_gate.ports.model_provider import ModelProviderError


@pytest.fixture
async def runs_client(run_repository, validator, provider_factory):
    provider = provider_factory()
    planning = PlanningService(provider, validator)
    app = create_app(planning, run_service=RunService(run_repository, planning))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        yield client, provider


def assert_error(response, status, code, run_id=None):
    assert response.status_code == status
    assert response.json() == {
        "run_id": run_id,
        "error": {"code": code.value, "message": RUN_MESSAGES[code]},
    }


async def test_create_location_get_and_legacy_unchanged(runs_client, run_repository, valid_plan):
    client, provider = runs_client
    response = await client.post("/api/runs?provider=evil&execute=true", json={"text": " Read \n"})
    assert response.status_code == 201
    run = response.json()
    assert run["state"] == "Ready" and run["version"] == 4 and run["intent"] == {"text": " Read \n"}
    assert response.headers["location"] == f"/api/runs/{run['run_id']}"
    rows = database_rows(run_repository.path)
    for _ in range(2):
        get = await client.get(response.headers["location"] + "?execute=true")
        assert get.status_code == 200 and get.json() == run
    assert database_rows(run_repository.path) == rows and provider.calls == 1
    legacy = await client.post("/api/plans", json={"text": "Read"})
    assert legacy.status_code == 200
    assert legacy.json() == {"state": "PlanValidated", "plan": valid_plan, "error": None}
    assert provider.calls == 2 and database_rows(run_repository.path) == rows
    assert (await client.get("/")).status_code == 200
    for path in ("/api/runs", "/api/runs/x/audit", "/api/runs/x/retry", "/api/runs/x/execute"):
        assert (await client.get(path)).status_code in {404, 405}


@pytest.mark.parametrize(
    "code", [ErrorCode.MODEL_TIMEOUT, ErrorCode.MODEL_UNAVAILABLE, ErrorCode.MODEL_RESPONSE_ERROR]
)
async def test_accepted_failed_run_is_201_and_retrievable(runs_client, code):
    client, provider = runs_client
    provider.error = ModelProviderError(code)
    response = await client.post("/api/runs", json={"text": "Read"})
    run = response.json()
    assert response.status_code == 201 and run["state"] == "PlanRejected" and run["plan"] is None
    assert run["error"] == {"code": code.value, "message": RUN_MESSAGES[code]}
    assert (await client.get(response.headers["location"])).json() == run and provider.calls == 1


@pytest.mark.parametrize(
    "body",
    [
        b"{",
        b"{}",
        b'{"text":""}',
        b'{"text":"a","text":"b"}',
        b"\xff",
        b'{"text":"\\ud800"}',
        json.dumps({"text": "界" * 3000}, ensure_ascii=False).encode(),
        *(
            json.dumps({"text": "Read", key: "override"}).encode()
            for key in [
                "run_id",
                "plan",
                "actor",
                "risk_class",
                "approval_required",
                "provider",
                "endpoint",
                "execute",
            ]
        ),
    ],
    ids=lambda raw: "body-" + hashlib.sha256(raw).hexdigest()[:8],
)
async def test_invalid_body_has_zero_rows_and_provider_calls(runs_client, run_repository, body):
    client, provider = runs_client
    response = await client.post(
        "/api/runs", content=body, headers={"content-type": "application/json"}
    )
    assert_error(response, 400, ErrorCode.INVALID_INTENT)
    assert (
        all(not rows for rows in database_rows(run_repository.path).values())
        and provider.calls == 0
    )


@pytest.mark.parametrize("endpoint", ["create", "approval"])
@pytest.mark.parametrize("media", [None, "text/plain", "application/json; charset=utf-16"])
async def test_media_types(runs_client, endpoint, media):
    client, provider = runs_client
    run_id = str(uuid4()) if endpoint == "approval" else None
    path = f"/api/runs/{run_id}/approval" if run_id else "/api/runs"
    response = await client.post(
        path, content=b"{}", headers={"content-type": media} if media else {}
    )
    assert_error(response, 415, ErrorCode.UNSUPPORTED_MEDIA_TYPE, run_id)
    assert provider.calls == 0


@pytest.mark.parametrize("endpoint", ["create", "approval"])
async def test_streamed_body_cap(runs_client, endpoint):
    client, provider = runs_client
    run_id = str(uuid4()) if endpoint == "approval" else None

    async def body():
        yield b"a" * 8000
        yield b"b" * 9000

    path = f"/api/runs/{run_id}/approval" if run_id else "/api/runs"
    response = await client.post(path, content=body(), headers={"content-type": "application/json"})
    assert_error(response, 413, ErrorCode.REQUEST_TOO_LARGE, run_id)
    assert provider.calls == 0


@pytest.mark.parametrize(
    "path_id", ["bad", "00000000-0000-1000-8000-000000000000", str(uuid4()).upper()]
)
async def test_path_precedes_media_and_body_validation(runs_client, path_id):
    client, provider = runs_client
    for method, suffix in [("GET", ""), ("POST", "/approval")]:
        response = await client.request(method, f"/api/runs/{path_id}{suffix}", content=b"invalid")
        assert_error(response, 404, RunErrorCode.RUN_NOT_FOUND)
    assert provider.calls == 0


async def test_unknown_id_body_lookup_version_state_precedence(runs_client, run_repository):
    client, _ = runs_client
    unknown = str(uuid4())
    assert_error(await client.get(f"/api/runs/{unknown}"), 404, RunErrorCode.RUN_NOT_FOUND, unknown)
    path = f"/api/runs/{unknown}/approval"
    assert_error(await client.post(path, json={}), 400, RunErrorCode.INVALID_APPROVAL, unknown)
    assert_error(
        await client.post(path, json={"decision": "APPROVE", "expected_version": 4}),
        404,
        RunErrorCode.RUN_NOT_FOUND,
        unknown,
    )
    run = advance(run_repository)
    path = f"/api/runs/{run.run_id}/approval"
    assert_error(
        await client.post(path, json={"decision": "APPROVE", "expected_version": 3}),
        409,
        RunErrorCode.STALE_RUN_VERSION,
        run.run_id,
    )
    assert_error(
        await client.post(path, json={"decision": "APPROVE", "expected_version": 4}),
        409,
        RunErrorCode.INVALID_RUN_TRANSITION,
        run.run_id,
    )


@pytest.mark.parametrize("choice", ["APPROVE", "REJECT"])
@pytest.mark.parametrize("reason", [None, "", "Local reason\t\r\n"])
async def test_approval_success_repeats_and_current_terminal_conflict(
    runs_client, run_repository, validator, choice, reason
):
    client, provider = runs_client
    provider.raw = json.dumps(plan_for(validator, Risk.SIDE_EFFECT).to_dict())
    response = await client.post("/api/runs", json={"text": "Append note"})
    run = response.json()
    path = response.headers["location"] + "/approval"
    body = {"decision": choice, "expected_version": 4}
    if reason is not None:
        body["reason"] = reason
    response = await client.post(path, json=body)
    assert response.status_code == 200 and response.json()["version"] == 5
    assert response.json()["state"] == ("Approved" if choice == "APPROVE" else "Rejected")
    assert response.json()["approval_required"] is True and response.json()["error"] is None
    before = database_rows(run_repository.path)
    assert before["approval_decisions"][0][3] == reason
    assert_error(
        await client.post(path, json=body), 409, RunErrorCode.STALE_RUN_VERSION, run["run_id"]
    )
    body["expected_version"] = 5
    assert_error(
        await client.post(path, json=body), 409, RunErrorCode.INVALID_RUN_TRANSITION, run["run_id"]
    )
    assert database_rows(run_repository.path) == before and provider.calls == 1


@pytest.mark.parametrize(
    "body",
    [
        b'{"decision":"APPROVE","expected_version":4.0}',
        b'{"decision":"APPROVE","expected_version":4,"reason":null}',
        b'{"decision":"APPROVE","decision":"REJECT","expected_version":4}',
        *(
            json.dumps({"decision": "APPROVE", "expected_version": 4, key: "override"}).encode()
            for key in [
                "actor_type",
                "actor_id",
                "run_id",
                "plan",
                "risk_class",
                "approval_required",
                "endpoint",
                "provider",
                "execute",
            ]
        ),
    ],
)
async def test_approval_override_rejection_before_lookup(runs_client, body):
    client, provider = runs_client
    run_id = str(uuid4())
    response = await client.post(
        f"/api/runs/{run_id}/approval", content=body, headers={"content-type": "application/json"}
    )
    assert_error(response, 400, RunErrorCode.INVALID_APPROVAL, run_id)
    assert provider.calls == 0


@pytest.mark.parametrize("endpoint", ["create", "get", "approval"])
async def test_storage_failures_are_safe_503(runs_client, run_repository, monkeypatch, endpoint):
    client, provider = runs_client
    run = advance(run_repository, RunState.AwaitingApproval, Risk.SIDE_EFFECT)
    operation = {"create": "create", "get": "get", "approval": "decide_approval"}[endpoint]

    def fail(*args):
        raise RunStoreError(RunErrorCode.DATABASE_UNAVAILABLE, args[0])

    monkeypatch.setattr(run_repository, operation, fail)
    if endpoint == "create":
        response = await client.post("/api/runs", json={"text": "Read"})
        known = response.json()["run_id"]
        assert known is not None
    else:
        known = run.run_id
        path = f"/api/runs/{known}"
        response = (
            await client.get(path)
            if endpoint == "get"
            else await client.post(
                path + "/approval", json={"decision": "APPROVE", "expected_version": 4}
            )
        )
    assert_error(response, 503, RunErrorCode.DATABASE_UNAVAILABLE, known)
    assert provider.calls == 0


async def test_slow_body_is_rejected_before_acceptance(runs_client, run_repository, monkeypatch):
    client, provider = runs_client
    monkeypatch.setattr(local_http, "REQUEST_TIMEOUT_SECONDS", 0.01)

    async def body():
        yield b'{"text":'
        import asyncio

        await asyncio.sleep(0.1)
        yield b'"Read"}'

    assert_error(
        await client.post(
            "/api/runs", content=body(), headers={"content-type": "application/json"}
        ),
        400,
        ErrorCode.INVALID_INTENT,
    )
    assert database_rows(run_repository.path)["runs"] == [] and provider.calls == 0


@pytest.mark.parametrize("state", list(RunState))
async def test_get_all_eight_states_is_pure_read(runs_client, run_repository, state):
    client, provider = runs_client
    risk = (
        Risk.SIDE_EFFECT
        if state in {RunState.AwaitingApproval, RunState.Approved, RunState.Rejected}
        else Risk.READ_ONLY
    )
    run = advance(run_repository, state, risk)
    before = database_rows(run_repository.path)
    response = await client.get(f"/api/runs/{run.run_id}")
    assert response.status_code == 200 and response.json() == run.to_dict()
    assert database_rows(run_repository.path) == before and provider.calls == 0
