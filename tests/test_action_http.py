import sqlite3
from contextlib import closing
from uuid import uuid4

import httpx
import pytest
from action_helpers import cmd
from test_service_demo_adapter import downstream

from agentic_intent_gate.api.local_http import create_app
from agentic_intent_gate.application.action_execution import ActionExecutionGateway
from agentic_intent_gate.application.generate_plan import PlanningService
from agentic_intent_gate.application.runs import RunService
from agentic_intent_gate.domain.run_validation import canonical_json
from agentic_intent_gate.infrastructure.security.local_capability import (
    CapabilityVerifier,
    LocalDemoAuthorization,
)


@pytest.fixture
async def http_env(demo_env, monkeypatch):
    e = demo_env

    class Provider:
        calls = 0

        async def generate_plan(self, *args, **kwargs):
            self.calls += 1
            return canonical_json(e.run.plan.to_dict())

    e.provider = Provider()
    original = e.executor.execute

    async def counted(*args):
        e.calls += 1
        return await original(*args)

    monkeypatch.setattr(e.executor, "execute", counted)
    e.planning = PlanningService(e.provider, e.runs.validator)
    e.gateway = ActionExecutionGateway(e.repo, e.policy, e.registry)
    e.app = create_app(
        e.planning,
        run_service=RunService(e.runs, e.planning),
        action_gateway=e.gateway,
        capability_verifier=e.verifier,
    )
    e.headers = {"Authorization": "Bearer " + e.token, "Idempotency-Key": str(uuid4())}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=e.app), base_url="http://127.0.0.1"
    ) as client:
        e.client = client
        yield e


def route(e, operation):
    if operation in {"admit", "list"}:
        return f"/api/runs/{e.run.run_id}/actions"
    return f"/api/actions/{e.action.action_id}" + (f"/{operation}" if operation != "get" else "")


@pytest.mark.parametrize("operation", ["admit", "list", "get", "authorize", "execute"])
@pytest.mark.parametrize("credential", [None, "Bearer invalid"])
async def test_all_five_routes_require_verified_capability(http_env, operation, credential):
    e = http_env
    before = e.ledger()
    headers = {"Idempotency-Key": str(uuid4())}
    if credential:
        headers["Authorization"] = credential
    response = await e.client.request(
        "GET" if operation in {"get", "list"} else "POST",
        route(e, operation),
        headers=headers,
        json={"expected_action_version": 1},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
    assert e.ledger() == before and e.calls == e.provider.calls == 0


async def test_real_http_flow_separate_gates_replay_and_pure_reads(http_env):
    e = http_env
    response = await e.client.post("/api/runs", json={"text": "Controlled new proposal"})
    assert response.status_code == 201
    run = response.json()
    assert run["state"] == "AwaitingApproval" and e.calls == 0
    approval = await e.client.post(
        f"/api/runs/{run['run_id']}/approval", json={"decision": "APPROVE", "expected_version": 4}
    )
    assert approval.status_code == 200 and e.calls == 0
    run_id = run["run_id"]
    admission_headers = dict(e.headers)
    result = await e.client.post(
        f"/api/runs/{run_id}/actions", headers=admission_headers, json={"expected_run_version": 5}
    )
    assert result.status_code == 201
    action = result.json()["actions"][0]
    assert action["state"] == "Pending" and e.calls == 0
    e.action = e.repo.get(action["action_id"])
    auth_headers = dict(e.headers, **{"Idempotency-Key": str(uuid4())})
    result = await e.client.post(
        route(e, "authorize"), headers=auth_headers, json={"expected_action_version": 1}
    )
    assert (
        result.status_code == 200
        and result.json()["action"]["state"] == "Authorized"
        and e.calls == 0
    )
    execute_headers = dict(e.headers, **{"Idempotency-Key": str(uuid4())})
    result = await e.client.post(
        route(e, "execute"), headers=execute_headers, json={"expected_action_version": 2}
    )
    assert result.status_code == 200 and result.json()["action"]["state"] == "Succeeded"
    assert e.calls == 1 and e.provider.calls == 1
    before = e.ledger(), downstream(e.demo_path)
    replay = await e.client.post(
        route(e, "execute"), headers=execute_headers, json={"expected_action_version": 2}
    )
    assert replay.status_code == 200 and replay.json()["replayed"]
    for path in (route(e, "get"), f"/api/runs/{run_id}/actions"):
        assert (await e.client.get(path, headers=e.headers)).status_code == 200
    assert before == (e.ledger(), downstream(e.demo_path)) and e.calls == 1
    assert e.token not in str(e.ledger()) + replay.text


@pytest.mark.parametrize(
    "raw",
    [
        b"{}",
        b'{"expected_action_version":true}',
        b'{"expected_action_version":1.0}',
        b'{"expected_action_version":1e0}',
        b'{"expected_action_version":1,"expected_action_version":1}',
        b'{"expected_action_version":1,"subject_id":"admin"}',
        b'{"expected_action_version":1,"role":"admin"}',
        b'{"expected_action_version":1,"tool_url":"https://evil.invalid"}',
        b'{"expected_action_version":1,"adapter_id":"evil"}',
        b'{"expected_action_version":1,"execution_scope":"evil"}',
        b'{"expected_action_version":1,"effect_key":"evil"}',
        b'{"expected_action_version":1,"arguments":{}}',
        b"\xff",
        b"null",
    ],
)
async def test_strict_body_overrides_never_authorize(http_env, raw):
    e = http_env
    before = e.ledger()
    response = await e.client.post(
        route(e, "authorize"), headers=e.headers | {"Content-Type": "application/json"}, content=raw
    )
    assert (
        response.status_code == 400 and response.json()["error"]["code"] == "INVALID_ACTION_REQUEST"
    )
    assert e.ledger() == before and e.calls == 0


@pytest.mark.parametrize(
    "case,status",
    [
        ("no_key", 428),
        ("bad_key", 400),
        ("media", 415),
        ("size", 413),
        ("origin", 403),
        ("local_other_origin", 403),
        ("query", 400),
        ("path", 400),
    ],
)
async def test_header_body_origin_limits_fail_closed(http_env, case, status):
    e = http_env
    headers = e.headers | {"Content-Type": "application/json"}
    body, path = b'{"expected_action_version":1}', route(e, "authorize")
    if case == "no_key":
        headers.pop("Idempotency-Key")
    elif case == "bad_key":
        headers["Idempotency-Key"] = "not-uuid"
    elif case == "media":
        headers["Content-Type"] = "text/plain"
    elif case == "size":
        body = b" " * 16385
    elif case in {"origin", "local_other_origin"}:
        headers["Origin"] = "https://evil.invalid" if case == "origin" else "http://127.0.0.1:9999"
    elif case == "query":
        path += "?token=not-authority"
    else:
        path = path.replace(e.action.action_id, e.action.action_id.upper())
    before = e.ledger()
    response = await e.client.post(path, headers=headers, content=body)
    assert response.status_code == status
    assert e.ledger() == before and e.calls == 0


async def test_cookie_query_localuser_and_unconfigured_verifier_never_grant(http_env):
    e = http_env
    for headers in ({"Cookie": "capability=" + e.token}, {"Authorization": "Bearer LOCAL_USER"}):
        assert (await e.client.get(route(e, "get"), headers=headers)).status_code == 401
    assert (await e.client.get(route(e, "get") + "?capability=" + e.token)).status_code == 401
    app = create_app(e.planning, action_gateway=e.gateway)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        assert (await client.get(route(e, "get"), headers=e.headers)).status_code == 401
    assert e.calls == 0


async def test_insufficient_operation_scope_denies_before_any_receipt_lookup(http_env):
    e = http_env
    limited = CapabilityVerifier(
        e.token, subject_id=e.principal.subject_id, binding=e.verifier.binding, operations=("read",)
    )
    policy = LocalDemoAuthorization(limited, policy_revision=e.policy.policy_revision)
    e.gateway = ActionExecutionGateway(e.repo, policy, e.registry)
    app = create_app(e.planning, action_gateway=e.gateway, capability_verifier=limited)
    before = e.ledger()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        assert (
            await client.post(
                route(e, "authorize"), headers=e.headers, json={"expected_action_version": 1}
            )
        ).status_code == 403
        assert (await client.get(route(e, "get"), headers=e.headers)).status_code == 200
    assert e.ledger() == before and e.calls == 0


@pytest.mark.parametrize("operation", ["authorize", "execute"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("normalized_arguments_json", '{"note":"unapproved","service_id":"service-demo"}'),
        ("target", "other"),
        ("tool_version", "2"),
        ("tool_name", "get_service_record"),
        ("spec_digest", "0" * 64),
        ("adapter_id", "evil"),
        ("execution_scope", str(uuid4())),
        ("approval_decision_id", str(uuid4())),
        ("risk_class", "READ_ONLY"),
    ],
)
async def test_stored_spec_tamper_blocks_each_effect_sensitive_http_gate(
    http_env, operation, field, value
):
    e = http_env
    version = 1
    if operation == "execute":
        e.gateway.authorize(cmd(e.action), e.principal)
        version = 2
    with closing(sqlite3.connect(e.runs.path)) as connection:
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(f"UPDATE actions SET {field}=?", (value,))
        connection.commit()
    before = e.ledger()
    result = await e.client.post(
        route(e, operation), headers=e.headers, json={"expected_action_version": version}
    )
    assert result.status_code == 503 and result.json()["error"]["code"] == "DATABASE_UNAVAILABLE"
    assert e.ledger() == before and e.calls == e.provider.calls == 0
    assert e.token not in result.text


async def test_executing_receipt_replay_is_202_without_dispatch(http_env):
    e = http_env
    authorized = e.gateway.authorize(cmd(e.action), e.principal).action
    request = cmd(authorized)
    e.repo.claim(request, e.principal, e.policy)
    before = e.ledger()
    response = await e.client.post(
        route(e, "execute"),
        headers=e.headers | {"Idempotency-Key": request.idempotency_key},
        json={"expected_action_version": 2},
    )
    assert response.status_code == 202 and response.json()["replayed"]
    assert response.json()["action"]["state"] == "Executing"
    assert e.calls == 0 and e.ledger() == before


@pytest.mark.parametrize("header", ["Authorization", "Idempotency-Key"])
async def test_duplicate_authority_or_key_headers_fail_closed(http_env, header):
    e = http_env
    headers = list(e.headers.items()) + [(header, e.headers[header])]
    before = e.ledger()
    response = await e.client.post(
        route(e, "authorize"), headers=headers, json={"expected_action_version": 1}
    )
    assert response.status_code == (401 if header == "Authorization" else 400)
    assert e.calls == 0 and e.ledger() == before


async def test_safe_http_failure_or_unknown_is_durable_result_not_rollback(http_env, monkeypatch):
    e = http_env
    e.gateway.authorize(cmd(e.action), e.principal)

    async def fault(*args):
        e.calls += 1
        raise RuntimeError(e.token + " private SQL statement and path")

    monkeypatch.setattr(e.executor, "execute", fault)
    response = await e.client.post(
        route(e, "execute"), headers=e.headers, json={"expected_action_version": 2}
    )
    assert response.status_code == 200 and response.json()["action"]["state"] == "Unknown"
    assert e.token not in response.text + str(e.ledger())
    assert response.headers.get("access-control-allow-credentials") is None


@pytest.mark.parametrize("operation", ["get", "authorize"])
async def test_runtime_schema_change_uses_frozen_action_store_error(http_env, operation):
    e = http_env
    with closing(sqlite3.connect(e.runs.path)) as connection:
        connection.execute("PRAGMA user_version=3")
    response = await e.client.request(
        "GET" if operation == "get" else "POST",
        route(e, operation),
        headers=e.headers,
        json={"expected_action_version": 1},
    )
    assert response.status_code == 503
    assert response.json()["error"] == {
        "code": "DATABASE_UNAVAILABLE",
        "message": "The Action store is unavailable.",
    }
    assert e.calls == 0
