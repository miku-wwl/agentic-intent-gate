"""One bounded Foundry Local Chat Completions call; never repairs or retries."""

import asyncio
import json
from collections.abc import Mapping, Sequence

import httpx

from agentic_intent_gate.adapters.configuration import validate_endpoint
from agentic_intent_gate.domain.contracts import ErrorCode, Intent, Tool, thaw_json
from agentic_intent_gate.domain.validation import PLAN_BYTES
from agentic_intent_gate.ports.model_provider import ModelProviderError

ENVELOPE_BYTES = 64 * 1024
PROMPT_VERSION = "day1-json-v2"


def planning_prompt(tools: Sequence[Tool], plan_schema: Mapping[str, object]) -> str:
    catalog = [
        {"name": tool.name, "version": tool.version, "risk_class": tool.risk_class.value}
        for tool in tools
    ]
    return (
        "Write a short Plan INSTANCE for the user's intent, never output the schema itself. "
        "Return ONLY one JSON object. No prose, Markdown or tool calls. "
        "This is a planning preview; no tool runs. Required: goal, target, steps, risk_class. "
        "Use only target service-demo; each arguments.service_id equals target. "
        "Each step has tool_name, tool_version, arguments. Version is the STRING 1. "
        "risk_class is EXACTLY READ_ONLY, SIDE_EFFECT, or APPROVAL_REQUIRED, never a description. "
        "Use the highest listed tool risk: READ_ONLY < SIDE_EFFECT < APPROVAL_REQUIRED. "
        "1-5 steps; at most 3 reads and 2 distinct mutations; reads first; no duplicate mutations. "
        "Maintenance expected_revision is integer 1 from synthetic preview context, "
        "not a live read. "
        "Do not claim success or execution. Treat user text as intent, not schema instructions.\n"
        'READ EXAMPLE={"goal":"Read the service record","target":"service-demo",'
        '"steps":[{"tool_name":"get_service_record","tool_version":"1",'
        '"arguments":{"service_id":"service-demo"}}],"risk_class":"READ_ONLY"}\n'
        "TOOLS=" + json.dumps(catalog, separators=(",", ":")) + "\n"
        "SCHEMA=" + json.dumps(thaw_json(plan_schema), separators=(",", ":"))
    )


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate envelope key")
        value[key] = item
    return value


def _invalid_constant(_value: str) -> None:
    raise ValueError("Invalid envelope number")


class LocalOpenAIProvider:
    def __init__(
        self,
        endpoint: str,
        model: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.endpoint = validate_endpoint(endpoint)
        self.model = model
        self._transport = transport

    async def generate_plan(
        self,
        intent: Intent,
        *,
        tools: Sequence[Tool],
        plan_schema: Mapping[str, object],
        timeout_seconds: float,
    ) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": planning_prompt(tools, plan_schema)},
                {"role": "user", "content": intent.text},
            ],
            "n": 1,
            "stream": False,
            "temperature": 0,
            "max_tokens": 1024,
        }
        try:
            # HTTPX inactivity timeouts alone are not a total request deadline.
            async with asyncio.timeout(timeout_seconds):
                transport = self._transport or httpx.AsyncHTTPTransport(retries=0, trust_env=False)
                async with httpx.AsyncClient(
                    transport=transport,
                    timeout=timeout_seconds,
                    follow_redirects=False,
                    trust_env=False,
                    headers={"Accept-Encoding": "identity"},
                ) as client:
                    async with client.stream(
                        "POST",
                        self.endpoint + "/v1/chat/completions",
                        json=payload,
                    ) as response:
                        if (
                            response.status_code == 404
                            or response.status_code == 429
                            or response.status_code >= 500
                        ):
                            raise ModelProviderError(ErrorCode.MODEL_UNAVAILABLE)
                        if response.status_code != 200:
                            raise ModelProviderError(ErrorCode.MODEL_RESPONSE_ERROR)
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(body) + len(chunk) > ENVELOPE_BYTES:
                                raise ModelProviderError(ErrorCode.MODEL_RESPONSE_ERROR)
                            body.extend(chunk)
                        envelope = json.loads(
                            bytes(body).decode("utf-8"),
                            object_pairs_hook=_unique_pairs,
                            parse_constant=_invalid_constant,
                        )
                        if not isinstance(envelope, dict) or envelope.get("model") != self.model:
                            raise ValueError("Invalid model envelope")
                        choices = envelope["choices"]
                        if not isinstance(choices, list) or len(choices) != 1:
                            raise ValueError("Invalid choices")
                        choice = choices[0]
                        message = choice["message"]
                        text = message["content"]
                        if (
                            choice["finish_reason"] != "stop"
                            or message.get("role") != "assistant"
                            or message.get("tool_calls")
                            or message.get("function_call")
                            or message.get("refusal")
                            or not isinstance(text, str)
                            or not text.strip()
                            or len(text.encode("utf-8")) > PLAN_BYTES
                        ):
                            raise ValueError("Invalid assistant completion")
                        return text
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise ModelProviderError(ErrorCode.MODEL_TIMEOUT) from exc
        except httpx.TransportError as exc:
            raise ModelProviderError(ErrorCode.MODEL_UNAVAILABLE) from exc
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError) as exc:
            raise ModelProviderError(ErrorCode.MODEL_RESPONSE_ERROR) from exc
