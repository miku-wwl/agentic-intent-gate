import asyncio
import json

import httpx
import pytest

from agentic_intent_gate.application.generate_plan import PlanningService
from agentic_intent_gate.config.configuration import Settings
from agentic_intent_gate.domain.contracts import ErrorCode, Intent
from agentic_intent_gate.infrastructure.llm.cloud import (
    ANTHROPIC_API_VERSION,
    AnthropicProvider,
    OpenAICloudProvider,
    OpenAICompatibleProvider,
)
from agentic_intent_gate.infrastructure.llm.local_openai import LocalOpenAIProvider
from agentic_intent_gate.infrastructure.llm.provider_factory import create_model_provider
from agentic_intent_gate.ports.model_provider import ModelProviderError

SECRET = "unit-test-provider-secret"


@pytest.mark.parametrize(
    "environment,expected_type",
    [
        (
            {
                "LOCAL_LLM_BASE_URL": "http://127.0.0.1:11434/v1",
                "LOCAL_LLM_MODEL": "local-model",
            },
            LocalOpenAIProvider,
        ),
        (
            {
                "AIG_LLM_PROVIDER": "openai",
                "AIG_LLM_MODEL": "test-model",
                "OPENAI_API_KEY": SECRET,
            },
            OpenAICloudProvider,
        ),
        (
            {
                "AIG_LLM_PROVIDER": "anthropic",
                "AIG_LLM_MODEL": "test-model",
                "ANTHROPIC_API_KEY": SECRET,
            },
            AnthropicProvider,
        ),
    ],
)
def test_factory_builds_selected_provider(environment, expected_type):
    assert isinstance(create_model_provider(Settings.from_environment(environment)), expected_type)


def test_factory_builds_generic_endpoint_provider(monkeypatch):
    import socket

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ],
    )
    settings = Settings.from_environment(
        {
            "AIG_LLM_PROVIDER": "openai-compatible",
            "AIG_LLM_MODEL": "test-model",
            "AIG_LLM_BASE_URL": "https://trusted-provider.com/v1",
            "AIG_LLM_API_KEY": SECRET,
        }
    )
    assert isinstance(create_model_provider(settings), OpenAICompatibleProvider)


def make_provider(kind, transport):
    if kind == "openai":
        return OpenAICloudProvider("test-model", SECRET, transport=transport)
    if kind == "anthropic":
        return AnthropicProvider("test-model", SECRET, transport=transport)
    return OpenAICompatibleProvider(
        "https://93.184.216.34/v1", "test-model", SECRET, transport=transport
    )


def openai_response(model="test-model", content='{"goal":"ok"}', **message_fields):
    message = {"role": "assistant", "content": content, **message_fields}
    return {
        "model": model,
        "choices": [{"message": message, "finish_reason": "stop"}],
    }


def anthropic_response(model="test-model", content='{"goal":"ok"}', stop_reason="end_turn"):
    return {
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": content}],
        "stop_reason": stop_reason,
    }


async def invoke(provider, validator, timeout_seconds=1):
    return await provider.generate_plan(
        Intent("Read service-demo"),
        tools=validator.tools,
        plan_schema=validator.plan_schema,
        timeout_seconds=timeout_seconds,
    )


@pytest.mark.parametrize("kind", ["openai", "anthropic", "compatible"])
async def test_provider_specific_wire_requests_and_authentication(validator, kind):
    calls = []

    def handle(request):
        calls.append(request)
        body = json.loads(request.content)
        if kind == "openai":
            assert request.url == "https://api.openai.com/v1/chat/completions"
            assert request.headers["authorization"] == f"Bearer {SECRET}"
            assert body["response_format"] == {"type": "json_object"}
            assert body["max_completion_tokens"] == 1024
            assert body["messages"][1] == {"role": "user", "content": "Read service-demo"}
            response = openai_response()
        elif kind == "anthropic":
            assert request.url == "https://api.anthropic.com/v1/messages"
            assert request.headers["x-api-key"] == SECRET
            assert request.headers["anthropic-version"] == ANTHROPIC_API_VERSION
            assert body["max_tokens"] == 1024
            assert body["messages"] == [{"role": "user", "content": "Read service-demo"}]
            assert "response_format" not in body and "tools" not in body
            response = anthropic_response()
        else:
            assert request.url == "https://93.184.216.34/v1/chat/completions"
            assert request.headers["authorization"] == f"Bearer {SECRET}"
            assert body["max_tokens"] == 1024 and body["stream"] is False
            assert "response_format" not in body
            response = openai_response()
        assert SECRET not in str(request.url)
        assert request.headers["accept-encoding"] == "identity"
        assert request.extensions["timeout"] == {
            "connect": 1,
            "read": 1,
            "write": 1,
            "pool": 1,
        }
        assert body["model"] == "test-model"
        if kind != "anthropic":
            assert body["stream"] is False
        else:
            assert "stream" not in body
        assert "SCHEMA=" in (
            body["system"] if kind == "anthropic" else body["messages"][0]["content"]
        )
        return httpx.Response(200, json=response)

    provider = make_provider(kind, httpx.MockTransport(handle))
    assert await invoke(provider, validator) == '{"goal":"ok"}'
    assert len(calls) == 1


@pytest.mark.parametrize(
    "status,code",
    [
        (302, ErrorCode.MODEL_RESPONSE_ERROR),
        (400, ErrorCode.MODEL_RESPONSE_ERROR),
        (401, ErrorCode.MODEL_RESPONSE_ERROR),
        (403, ErrorCode.MODEL_RESPONSE_ERROR),
        (404, ErrorCode.MODEL_RESPONSE_ERROR),
        (429, ErrorCode.MODEL_UNAVAILABLE),
        (500, ErrorCode.MODEL_UNAVAILABLE),
        (503, ErrorCode.MODEL_UNAVAILABLE),
        (408, ErrorCode.MODEL_TIMEOUT),
    ],
)
@pytest.mark.parametrize("kind", ["openai", "anthropic", "compatible"])
async def test_http_statuses_fail_closed_without_retry(validator, status, code, kind):
    calls = []

    def handle(_request):
        calls.append(1)
        return httpx.Response(
            status,
            headers={"location": "https://untrusted.example/redirect"},
            text="private provider response and secret=" + SECRET,
        )

    with pytest.raises(ModelProviderError) as caught:
        await invoke(make_provider(kind, httpx.MockTransport(handle)), validator)
    assert caught.value.code == code
    assert len(calls) == 1
    assert SECRET not in str(caught.value)
    assert "private provider response" not in str(caught.value)


@pytest.mark.parametrize("kind", ["openai", "anthropic", "compatible"])
@pytest.mark.parametrize(
    "failure,code",
    [
        (httpx.ConnectError("private details " + SECRET), ErrorCode.MODEL_UNAVAILABLE),
        (httpx.ReadTimeout("private details " + SECRET), ErrorCode.MODEL_TIMEOUT),
    ],
)
async def test_network_errors_are_classified_without_leaking_details(
    validator, kind, failure, code
):
    with pytest.raises(ModelProviderError) as caught:
        await invoke(
            make_provider(
                kind, httpx.MockTransport(lambda _request: (_ for _ in ()).throw(failure))
            ),
            validator,
        )
    assert caught.value.code == code
    assert "private details" not in str(caught.value)
    assert SECRET not in str(caught.value)


@pytest.mark.parametrize("kind", ["openai", "compatible"])
@pytest.mark.parametrize(
    "response",
    [
        {"model": "another-model", "choices": []},
        {"model": "test-model", "choices": []},
        {"model": "test-model", "choices": [{"finish_reason": "stop"}]},
        openai_response(refusal="no"),
        openai_response(tool_calls=[{"id": "call"}]),
        openai_response(content=None),
        openai_response(content=""),
        {**openai_response(), "choices": [{"finish_reason": "length"}]},
        {**openai_response(), "choices": [{"finish_reason": "content_filter"}]},
    ],
)
async def test_openai_response_rejections(validator, kind, response):
    # Use normal top-level OpenAI shapes for all OpenAI-compatible response adapters.
    provider = make_provider(
        kind, httpx.MockTransport(lambda _request: httpx.Response(200, json=response))
    )
    with pytest.raises(ModelProviderError) as caught:
        await invoke(provider, validator)
    assert caught.value.code == ErrorCode.MODEL_RESPONSE_ERROR


@pytest.mark.parametrize(
    "response",
    [
        anthropic_response(model="wrong-model"),
        anthropic_response(stop_reason="refusal"),
        anthropic_response(stop_reason="max_tokens"),
        anthropic_response(stop_reason="pause_turn"),
        anthropic_response(stop_reason="tool_use"),
        anthropic_response(stop_reason=None),
        anthropic_response(content=""),
        anthropic_response(content="x" * (16 * 1024 + 1)),
        {**anthropic_response(), "content": []},
        {**anthropic_response(), "content": [{"type": "tool_use", "id": "toolu"}]},
        {
            **anthropic_response(),
            "content": [{"type": "text", "text": "{}"}, {"type": "text", "text": "{}"}],
        },
    ],
)
async def test_anthropic_response_rejections(validator, response):
    provider = AnthropicProvider(
        "test-model",
        SECRET,
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=response)),
    )
    with pytest.raises(ModelProviderError) as caught:
        await invoke(provider, validator)
    assert caught.value.code == ErrorCode.MODEL_RESPONSE_ERROR


@pytest.mark.parametrize(
    "body",
    [
        b"{",
        b"\xff",
        b'{"model":"test-model","model":"other","choices":[]}',
        b'{"usage":NaN}',
        b"x" * (64 * 1024 + 1),
    ],
    ids=["invalid-json", "invalid-utf8", "duplicate-key", "nonfinite", "oversized"],
)
@pytest.mark.parametrize("kind", ["openai", "anthropic", "compatible"])
async def test_malformed_and_oversized_envelopes_are_rejected(validator, body, kind):
    provider = make_provider(
        kind, httpx.MockTransport(lambda _request: httpx.Response(200, content=body))
    )
    with pytest.raises(ModelProviderError) as caught:
        await invoke(provider, validator)
    assert caught.value.code == ErrorCode.MODEL_RESPONSE_ERROR


@pytest.mark.parametrize("kind", ["openai", "anthropic", "compatible"])
async def test_total_timeout_stops_a_trickling_response(validator, kind):
    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"{"
            await asyncio.sleep(1)
            yield b"}"

    calls = []

    def handle(_request):
        calls.append(1)
        return httpx.Response(200, stream=SlowStream())

    with pytest.raises(ModelProviderError) as caught:
        await invoke(
            make_provider(kind, httpx.MockTransport(handle)), validator, timeout_seconds=0.03
        )
    assert caught.value.code == ErrorCode.MODEL_TIMEOUT
    assert len(calls) == 1


async def test_model_alias_snapshot_is_accepted_but_plan_json_is_validated(validator, valid_plan):
    raw_plan = json.dumps(valid_plan)
    provider = OpenAICloudProvider(
        "gpt-4.1",
        SECRET,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200, json=openai_response(model="gpt-4.1-2025-04-14", content=raw_plan)
            )
        ),
    )
    result = await PlanningService(provider, validator).generate_plan(Intent("Read service-demo"))
    assert result.plan is not None and result.error is None


async def test_provider_text_that_is_not_json_is_rejected_by_the_existing_validator(validator):
    provider = OpenAICloudProvider(
        "test-model",
        SECRET,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json=openai_response(content="not JSON"))
        ),
    )
    result = await PlanningService(provider, validator).generate_plan(Intent("Read service-demo"))
    assert result.plan is None
    assert result.error is not None and result.error.code == ErrorCode.PLAN_VALIDATION_ERROR


@pytest.mark.parametrize("kind", ["openai", "anthropic", "compatible"])
async def test_cancellation_propagates_and_closes_response_without_retry(validator, kind):
    started = asyncio.Event()
    closed = asyncio.Event()
    calls = []

    class WaitingStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            started.set()
            await asyncio.Event().wait()
            yield b"{}"

        async def aclose(self):
            closed.set()

    def handle(_request):
        calls.append(1)
        return httpx.Response(200, stream=WaitingStream())

    provider = make_provider(kind, httpx.MockTransport(handle))
    task = asyncio.create_task(invoke(provider, validator))
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert closed.is_set()
    assert len(calls) == 1


@pytest.mark.parametrize("status", [200, 503])
async def test_default_provider_never_sends_cloud_credentials_or_falls_back(
    validator, valid_plan, status
):
    settings = Settings.from_environment(
        {
            "LOCAL_LLM_BASE_URL": "http://127.0.0.1:11434/v1",
            "LOCAL_LLM_MODEL": "test-model",
            "OPENAI_API_KEY": SECRET,
            "ANTHROPIC_API_KEY": SECRET,
            "AIG_LLM_API_KEY": SECRET,
        }
    )
    calls = []

    def handle(request):
        calls.append(request)
        assert request.url == "http://127.0.0.1:11434/v1/chat/completions"
        assert "authorization" not in request.headers and "x-api-key" not in request.headers
        assert SECRET not in request.content.decode() and SECRET not in str(request.headers)
        return httpx.Response(status, json=openai_response(content=json.dumps(valid_plan)))

    provider = create_model_provider(settings, transport=httpx.MockTransport(handle))
    assert isinstance(provider, LocalOpenAIProvider)
    result = await PlanningService(provider, validator).generate_plan(Intent("Read service-demo"))
    if status == 200:
        assert result.plan is not None and result.error is None
    else:
        assert result.plan is None and result.error.code == ErrorCode.MODEL_UNAVAILABLE
    assert len(calls) == 1
