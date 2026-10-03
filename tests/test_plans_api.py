import asyncio
import json

import httpx
import pytest

from agentic_intent_gate.adapters.local_http import create_app
from agentic_intent_gate.application.generate_plan import PlanningService
from agentic_intent_gate.domain.contracts import ErrorCode, Intent, PlanningState
from agentic_intent_gate.ports.model_provider import ModelProviderError


async def post(service, *, content=None, headers=None, body=None):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(service)),
        base_url="http://127.0.0.1",
    ) as client:
        if body is not None:
            return await client.post("/api/plans", json=body)
        return await client.post("/api/plans", content=content, headers=headers)


async def test_success_full_route(validator, provider_factory, valid_plan):
    provider = provider_factory()
    response = await post(PlanningService(provider, validator), body={"text": "Read service-demo"})
    assert response.status_code == 200
    assert response.json() == {"state": "PlanValidated", "plan": valid_plan, "error": None}
    assert provider.calls == 1


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"{",
        b'{"text":""}',
        b'{"text":"a","text":"b"}',
        b'{"text":"a","provider":"remote"}',
        b'{"text":NaN}',
        b"\xff",
        json.dumps({"text": "界" * 3000}, ensure_ascii=False).encode(),
    ],
    ids=["empty", "malformed", "blank", "duplicate", "extra", "nonfinite", "utf8", "text-bytes"],
)
async def test_request_failures_have_zero_model_calls(validator, provider_factory, raw):
    provider = provider_factory()
    response = await post(
        PlanningService(provider, validator),
        content=raw,
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 400
    assert response.json()["state"] is None
    assert response.json()["plan"] is None
    assert response.json()["error"]["code"] == "INVALID_INTENT"
    assert provider.calls == 0


@pytest.mark.parametrize("content_type", [None, "text/plain", "application/json; charset=utf-16"])
async def test_media_type_rejection(validator, provider_factory, content_type):
    provider = provider_factory()
    headers = {} if content_type is None else {"content-type": content_type}
    response = await post(PlanningService(provider, validator), content=b"{}", headers=headers)
    assert response.status_code == 415
    assert response.json()["state"] is None
    assert provider.calls == 0


async def test_chunked_request_limit(validator, provider_factory):
    async def body():
        yield b"a" * 8000
        yield b"b" * 9000

    provider = provider_factory()
    response = await post(
        PlanningService(provider, validator),
        content=body(),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "REQUEST_TOO_LARGE"
    assert provider.calls == 0


@pytest.mark.parametrize(
    "raw,phase",
    [
        ("Here is your plan: {}", "PARSE_"),
        ('{"goal":"x"}', "SCHEMA_"),
        (
            '{"goal":"x","target":"service-demo","steps":[{"tool_name":"get_service_record","tool_version":"1","arguments":{"service_id":"service-demo"}}],"risk_class":"SIDE_EFFECT"}',
            "SEMANTIC_",
        ),
    ],
)
async def test_bad_model_output_fails_closed(validator, provider_factory, raw, phase):
    provider = provider_factory(raw=raw)
    response = await post(PlanningService(provider, validator), body={"text": "Read service-demo"})
    assert response.status_code == 422
    data = response.json()
    assert data["state"] == "PlanRejected" and data["plan"] is None
    assert data["error"]["code"] == "PLAN_VALIDATION_ERROR"
    assert data["error"]["issues"][0]["code"].startswith(phase)
    assert provider.calls == 1


@pytest.mark.parametrize(
    "code,status",
    [
        (ErrorCode.MODEL_TIMEOUT, 504),
        (ErrorCode.MODEL_UNAVAILABLE, 503),
        (ErrorCode.MODEL_RESPONSE_ERROR, 502),
    ],
)
async def test_provider_failures_have_frozen_http_codes(validator, provider_factory, code, status):
    provider = provider_factory(error=ModelProviderError(code))
    response = await post(PlanningService(provider, validator), body={"text": "Read service-demo"})
    assert response.status_code == status
    assert response.json()["state"] == "PlanRejected"
    assert response.json()["plan"] is None
    assert response.json()["error"]["code"] == code.value
    assert provider.calls == 1


async def test_unexpected_failure_is_sanitized(validator, provider_factory):
    provider = provider_factory(error=RuntimeError("sensitive-provider-details"))
    response = await post(PlanningService(provider, validator), body={"text": "Read service-demo"})
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "sensitive-provider-details" not in response.text


async def test_service_states_and_valid_mock_plan(validator, provider_factory):
    result = await PlanningService(provider_factory(), validator).generate_plan(
        Intent("Read service-demo")
    )
    assert result.states == (
        PlanningState.IntentReceived,
        PlanningState.PlanGenerating,
        PlanningState.PlanGenerated,
        PlanningState.PlanValidated,
    )


async def test_service_empty_intent_does_not_call_provider(validator, provider_factory):
    provider = provider_factory()
    result = await PlanningService(provider, validator).generate_plan(Intent(""))
    assert result.http_status == 400 and result.state is None and provider.calls == 0


async def test_service_invalid_candidate_records_generation_then_rejection(
    validator, provider_factory
):
    result = await PlanningService(provider_factory(raw="{}"), validator).generate_plan(
        Intent("Read")
    )
    assert result.states == (
        PlanningState.IntentReceived,
        PlanningState.PlanGenerating,
        PlanningState.PlanGenerated,
        PlanningState.PlanRejected,
    )


async def test_service_total_timeout_is_not_delegated_to_provider(validator):
    class SlowProvider:
        calls = 0

        async def generate_plan(self, intent, **kwargs):
            self.calls += 1
            await asyncio.sleep(1)

    provider = SlowProvider()
    result = await PlanningService(provider, validator, model_timeout_seconds=0.02).generate_plan(
        Intent("Read")
    )
    assert result.http_status == 504 and result.plan is None and provider.calls == 1
    assert PlanningState.PlanGenerated not in result.states


async def test_backend_has_no_ui_routes(validator, provider_factory):
    app = create_app(PlanningService(provider_factory(), validator))
    assert [(route.path, route.methods) for route in app.routes] == [("/api/plans", {"POST"})]
