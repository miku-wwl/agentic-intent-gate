"""Bounded cloud and generic OpenAI-compatible ModelProvider adapters."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Mapping, Sequence

import httpx

from agentic_intent_gate.config.configuration import (
    ANTHROPIC_ENDPOINT,
    OPENAI_ENDPOINT,
    validate_openai_compatible_endpoint,
)
from agentic_intent_gate.domain.contracts import ErrorCode, Intent, Tool
from agentic_intent_gate.domain.validation import PLAN_BYTES
from agentic_intent_gate.infrastructure.llm.local_openai import planning_prompt
from agentic_intent_gate.ports.model_provider import ModelProviderError

ENVELOPE_BYTES = 64 * 1024
MAX_COMPLETION_TOKENS = 1024
ANTHROPIC_API_VERSION = "2023-06-01"


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate response key")
        value[key] = item
    return value


def _invalid_constant(_value: str) -> None:
    raise ValueError("Invalid response number")


def _model_matches(requested: str, reported: object) -> bool:
    if not isinstance(reported, str):
        return False
    if reported == requested:
        return True
    # First-party APIs may report a dated snapshot for a configured alias.
    return bool(
        re.fullmatch(re.escape(requested) + r"-\d{4}-\d{2}-\d{2}", reported)
        or re.fullmatch(re.escape(requested) + r"-\d{8}", reported)
    )


def _assistant_text(envelope: object, model: str) -> str:
    if not isinstance(envelope, dict) or not _model_matches(model, envelope.get("model")):
        raise ValueError("Invalid model response")

    choices = envelope["choices"]
    if not isinstance(choices, list) or len(choices) != 1:
        raise ValueError("Invalid choices")
    choice = choices[0]
    if not isinstance(choice, dict) or choice.get("finish_reason") != "stop":
        raise ValueError("Incomplete completion")
    message = choice["message"]
    if not isinstance(message, dict):
        raise ValueError("Invalid message")
    text = message["content"]
    if (
        message.get("role") != "assistant"
        or message.get("tool_calls") not in (None, [])
        or message.get("function_call") is not None
        or message.get("refusal") is not None
        or not isinstance(text, str)
        or not text.strip()
        or len(text.encode("utf-8")) > PLAN_BYTES
    ):
        raise ValueError("Invalid assistant completion")
    return text


def _anthropic_text(envelope: object, model: str) -> str:
    if (
        not isinstance(envelope, dict)
        or envelope.get("type") != "message"
        or envelope.get("role") != "assistant"
        or not _model_matches(model, envelope.get("model"))
        or envelope.get("stop_reason") != "end_turn"
    ):
        raise ValueError("Invalid or refused Anthropic message")
    content = envelope["content"]
    if not isinstance(content, list) or len(content) != 1:
        raise ValueError("Invalid Anthropic content")
    block = content[0]
    if not isinstance(block, dict) or block.get("type") != "text":
        raise ValueError("Invalid Anthropic text block")
    text = block.get("text")
    if not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > PLAN_BYTES:
        raise ValueError("Invalid Anthropic text")
    return text


async def _post_json(
    endpoint: str,
    payload: Mapping[str, object],
    *,
    headers: Mapping[str, str],
    timeout_seconds: float,
    transport: httpx.AsyncBaseTransport | None,
) -> object:
    if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool):
        raise ModelProviderError(ErrorCode.MODEL_RESPONSE_ERROR)
    if not 0 < timeout_seconds <= 28:
        raise ModelProviderError(ErrorCode.MODEL_RESPONSE_ERROR)
    try:
        async with asyncio.timeout(timeout_seconds):
            active_transport = transport or httpx.AsyncHTTPTransport(retries=0, trust_env=False)
            async with httpx.AsyncClient(
                transport=active_transport,
                timeout=httpx.Timeout(timeout_seconds, connect=min(2.0, timeout_seconds)),
                follow_redirects=False,
                trust_env=False,
                headers={"Accept-Encoding": "identity", **headers},
            ) as client:
                async with client.stream("POST", endpoint, json=payload) as response:
                    if response.status_code == 408:
                        raise ModelProviderError(ErrorCode.MODEL_TIMEOUT)
                    if response.status_code == 429 or response.status_code >= 500:
                        raise ModelProviderError(ErrorCode.MODEL_UNAVAILABLE)
                    if response.status_code != 200:
                        raise ModelProviderError(ErrorCode.MODEL_RESPONSE_ERROR)
                    if response.headers.get("content-encoding", "identity").strip().lower() != (
                        "identity"
                    ):
                        raise ModelProviderError(ErrorCode.MODEL_RESPONSE_ERROR)
                    content_length = response.headers.get("content-length")
                    if content_length is not None:
                        try:
                            if int(content_length) > ENVELOPE_BYTES:
                                raise ModelProviderError(ErrorCode.MODEL_RESPONSE_ERROR)
                        except ValueError:
                            raise ModelProviderError(ErrorCode.MODEL_RESPONSE_ERROR) from None
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > ENVELOPE_BYTES:
                            raise ModelProviderError(ErrorCode.MODEL_RESPONSE_ERROR)
                        body.extend(chunk)
        return json.loads(
            bytes(body).decode("utf-8"),
            object_pairs_hook=_unique_pairs,
            parse_constant=_invalid_constant,
        )
    except ModelProviderError:
        raise
    except (TimeoutError, httpx.TimeoutException) as exc:
        raise ModelProviderError(ErrorCode.MODEL_TIMEOUT) from exc
    except httpx.TransportError as exc:
        raise ModelProviderError(ErrorCode.MODEL_UNAVAILABLE) from exc
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError) as exc:
        raise ModelProviderError(ErrorCode.MODEL_RESPONSE_ERROR) from exc


class _CloudProvider:
    endpoint: str

    def __init__(
        self,
        endpoint: str,
        model: str,
        api_key: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if (
            not isinstance(api_key, str)
            or not api_key
            or len(api_key) > 8192
            or api_key != api_key.strip()
            or any(not 33 <= ord(character) <= 126 for character in api_key)
        ):
            raise ValueError("A valid cloud API key is required.")
        self.endpoint = endpoint
        self.model = model
        self._api_key = api_key
        self._transport = transport


class OpenAICloudProvider(_CloudProvider):
    """First-party OpenAI Chat Completions adapter using JSON mode."""

    def __init__(
        self,
        model: str,
        api_key: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(OPENAI_ENDPOINT + "/chat/completions", model, api_key, transport=transport)

    async def generate_plan(
        self,
        intent: Intent,
        *,
        tools: Sequence[Tool],
        plan_schema: Mapping[str, object],
        timeout_seconds: float,
    ) -> str:
        prompt = planning_prompt(tools, plan_schema)
        envelope = await _post_json(
            self.endpoint,
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": intent.text},
                ],
                "n": 1,
                "stream": False,
                "max_completion_tokens": MAX_COMPLETION_TOKENS,
                "response_format": {"type": "json_object"},
            },
            headers={
                "Authorization": "Bearer " + self._api_key,
                "Content-Type": "application/json",
            },
            timeout_seconds=timeout_seconds,
            transport=self._transport,
        )
        try:
            return _assistant_text(envelope, self.model)
        except (KeyError, TypeError, ValueError, UnicodeError, RecursionError) as exc:
            raise ModelProviderError(ErrorCode.MODEL_RESPONSE_ERROR) from exc


class OpenAICompatibleProvider(_CloudProvider):
    """Generic operator-configured chat adapter using only common request fields."""

    def __init__(
        self,
        endpoint: str,
        model: str,
        api_key: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        base_url = validate_openai_compatible_endpoint(endpoint)
        super().__init__(base_url + "/chat/completions", model, api_key, transport=transport)

    async def generate_plan(
        self,
        intent: Intent,
        *,
        tools: Sequence[Tool],
        plan_schema: Mapping[str, object],
        timeout_seconds: float,
    ) -> str:
        envelope = await _post_json(
            self.endpoint,
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": planning_prompt(tools, plan_schema)},
                    {"role": "user", "content": intent.text},
                ],
                "stream": False,
                "max_tokens": MAX_COMPLETION_TOKENS,
            },
            headers={
                "Authorization": "Bearer " + self._api_key,
                "Content-Type": "application/json",
            },
            timeout_seconds=timeout_seconds,
            transport=self._transport,
        )
        try:
            return _assistant_text(envelope, self.model)
        except (KeyError, TypeError, ValueError, UnicodeError, RecursionError) as exc:
            raise ModelProviderError(ErrorCode.MODEL_RESPONSE_ERROR) from exc


class AnthropicProvider(_CloudProvider):
    """Native Anthropic Messages API adapter; it does not emulate Chat Completions."""

    def __init__(
        self,
        model: str,
        api_key: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(ANTHROPIC_ENDPOINT + "/messages", model, api_key, transport=transport)

    async def generate_plan(
        self,
        intent: Intent,
        *,
        tools: Sequence[Tool],
        plan_schema: Mapping[str, object],
        timeout_seconds: float,
    ) -> str:
        envelope = await _post_json(
            self.endpoint,
            {
                "model": self.model,
                "max_tokens": MAX_COMPLETION_TOKENS,
                "system": planning_prompt(tools, plan_schema),
                "messages": [{"role": "user", "content": intent.text}],
            },
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            timeout_seconds=timeout_seconds,
            transport=self._transport,
        )
        try:
            return _anthropic_text(envelope, self.model)
        except (KeyError, TypeError, ValueError, UnicodeError, RecursionError) as exc:
            raise ModelProviderError(ErrorCode.MODEL_RESPONSE_ERROR) from exc
