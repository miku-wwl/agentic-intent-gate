import asyncio
import json
import time

import httpx
import pytest

from agentic_intent_gate.domain.contracts import ErrorCode, Intent
from agentic_intent_gate.infrastructure.llm.local_openai import LocalOpenAIProvider
from agentic_intent_gate.ports.model_provider import ModelProviderError


async def call(validator, handler, *, timeout_seconds=1):
    provider = LocalOpenAIProvider(
        "http://127.0.0.1:12345",
        "phi-test",
        transport=httpx.MockTransport(handler),
    )
    return await provider.generate_plan(
        Intent("Read service-demo"),
        tools=validator.tools,
        plan_schema=validator.plan_schema,
        timeout_seconds=timeout_seconds,
    )


async def test_exact_wire_request_and_raw_return(validator, envelope, valid_plan, monkeypatch):
    calls = []
    raw = json.dumps(valid_plan, indent=2)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")

    def handler(request):
        calls.append(request)
        body = json.loads(request.content)
        assert str(request.url) == "http://127.0.0.1:12345/v1/chat/completions"
        assert request.method == "POST"
        assert body["model"] == "phi-test"
        assert body["stream"] is False and body["n"] == 1
        assert body["temperature"] == 0 and body["max_tokens"] == 1024
        assert body["response_format"] == {"type": "json_object"}
        assert body["messages"][1] == {"role": "user", "content": "Read service-demo"}
        assert "SCHEMA=" in body["messages"][0]["content"]
        assert "Information disclosure risk" not in body["messages"][0]["content"]
        assert "tools" not in body and "functions" not in body
        return httpx.Response(200, json=envelope(raw))

    assert await call(validator, handler) == raw
    assert len(calls) == 1


@pytest.mark.parametrize(
    "status,code",
    [
        (404, ErrorCode.MODEL_UNAVAILABLE),
        (429, ErrorCode.MODEL_UNAVAILABLE),
        (500, ErrorCode.MODEL_UNAVAILABLE),
        (503, ErrorCode.MODEL_UNAVAILABLE),
        (400, ErrorCode.MODEL_RESPONSE_ERROR),
        (401, ErrorCode.MODEL_RESPONSE_ERROR),
        (302, ErrorCode.MODEL_RESPONSE_ERROR),
    ],
)
async def test_http_failure_and_redirect_never_retry(validator, status, code):
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        return httpx.Response(
            status, headers={"location": "https://example.com"}, text="sensitive upstream body"
        )

    with pytest.raises(ModelProviderError) as caught:
        await call(validator, handler)
    assert caught.value.code == code and count == 1
    assert "sensitive upstream body" not in str(caught.value)


@pytest.mark.parametrize(
    "failure,code",
    [
        (httpx.ConnectError("private details"), ErrorCode.MODEL_UNAVAILABLE),
        (httpx.ReadTimeout("private details"), ErrorCode.MODEL_TIMEOUT),
    ],
)
async def test_transport_error_mapping(validator, failure, code):
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        raise failure

    with pytest.raises(ModelProviderError) as caught:
        await call(validator, handler)
    assert caught.value.code == code and count == 1


@pytest.mark.parametrize(
    "kind",
    [
        "missing_choices",
        "empty_choices",
        "multiple_choices",
        "missing_message",
        "non_object_choice",
        "non_object_message",
        "empty_content",
        "blank_content",
        "nonstring_content",
        "length",
        "tool_calls",
        "function_call",
        "refusal",
        "wrong_role",
        "wrong_model",
        "oversize_content",
        "null_envelope",
    ],
)
async def test_envelope_rejection(validator, envelope, kind):
    value = envelope()
    choice = value["choices"][0]
    message = choice["message"]
    if kind == "missing_choices":
        value.pop("choices")
    elif kind == "empty_choices":
        value["choices"] = []
    elif kind == "multiple_choices":
        value["choices"] *= 2
    elif kind == "missing_message":
        choice.pop("message")
    elif kind == "non_object_choice":
        value["choices"] = [1]
    elif kind == "non_object_message":
        choice["message"] = []
    elif kind == "empty_content":
        message["content"] = ""
    elif kind == "blank_content":
        message["content"] = " \t"
    elif kind == "nonstring_content":
        message["content"] = {}
    elif kind == "length":
        choice["finish_reason"] = "length"
    elif kind == "tool_calls":
        message["tool_calls"] = [{"function": "x"}]
    elif kind == "function_call":
        message["function_call"] = {"name": "x"}
    elif kind == "refusal":
        message["refusal"] = "no"
    elif kind == "wrong_role":
        message["role"] = "user"
    elif kind == "wrong_model":
        value["model"] = "another-model"
    elif kind == "oversize_content":
        message["content"] = "x" * (16 * 1024 + 1)
    elif kind == "null_envelope":
        value = None
    with pytest.raises(ModelProviderError) as caught:
        await call(validator, lambda request: httpx.Response(200, json=value))
    assert caught.value.code == ErrorCode.MODEL_RESPONSE_ERROR


@pytest.mark.parametrize(
    "body",
    [
        b"{",
        b"\xff",
        b"x" * (64 * 1024 + 1),
        b'{"model":"phi-test","model":"other"}',
        b'{"usage":NaN}',
    ],
    ids=["malformed", "invalid-utf8", "oversized-envelope", "duplicate-key", "nonfinite"],
)
async def test_raw_envelope_bounds_and_parsing(validator, body):
    with pytest.raises(ModelProviderError) as caught:
        await call(validator, lambda request: httpx.Response(200, content=body))
    assert caught.value.code == ErrorCode.MODEL_RESPONSE_ERROR


async def test_total_timeout_interrupts_trickling_response(validator, envelope):
    encoded = json.dumps(envelope()).encode()

    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield encoded[:10]
            await asyncio.sleep(1)
            yield encoded[10:]

    count = 0

    def handler(request):
        nonlocal count
        count += 1
        return httpx.Response(200, stream=SlowStream())

    started = time.monotonic()
    with pytest.raises(ModelProviderError) as caught:
        await call(validator, handler, timeout_seconds=0.03)
    assert caught.value.code == ErrorCode.MODEL_TIMEOUT and count == 1
    assert time.monotonic() - started < 0.5
