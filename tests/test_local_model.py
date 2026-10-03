"""Explicit opt-in real provider lane: pytest -m live. Never falls back or skips."""

import asyncio
import json
import os
import time
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import pytest

from agentic_intent_gate.adapters.configuration import Settings
from agentic_intent_gate.adapters.models.local_openai import PROMPT_VERSION, LocalOpenAIProvider
from agentic_intent_gate.domain.contracts import Intent, thaw_json
from agentic_intent_gate.domain.validation import ValidationFailure
from agentic_intent_gate.ports.model_provider import ModelProviderError

pytestmark = pytest.mark.live


def write_evidence(evidence):
    output = Path(os.environ.get("LIVE_MODEL_EVIDENCE_DIR", ".runtime/live-model"))
    output.mkdir(parents=True, exist_ok=True)
    record = output / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    record.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def test_real_phi4_mini_raw_output_and_validation(validator):
    settings = Settings.from_environment()
    provider = LocalOpenAIProvider(settings.endpoint, settings.model)
    intent = Intent("Plan to read the service-demo record.")
    evidence = {
        "recorded_at_utc": datetime.now(UTC).isoformat(),
        "lane": "real-local-provider-no-mock",
        "prompt_version": PROMPT_VERSION,
        "endpoint": settings.endpoint,
        "wire_model": settings.model,
        "wire_method": "POST",
        "wire_path": "/v1/chat/completions",
        "request_parameters": {"n": 1, "stream": False, "temperature": 0, "max_tokens": 1024},
        "intent": intent.text,
        "schema": thaw_json(validator.plan_schema),
        "versions": {name: version(name) for name in ("httpx", "jsonschema", "fastapi", "uvicorn")},
        "raw_output": None,
        "validation": "FAIL",
        "tool_invocations": 0,
        "automatic_retries_or_repair_calls": 0,
    }
    started = time.monotonic()
    try:
        raw = await provider.generate_plan(
            intent,
            tools=validator.tools,
            plan_schema=validator.plan_schema,
            timeout_seconds=settings.model_timeout_seconds,
        )
        evidence["raw_output"] = raw
        plan = validator.parse_plan(raw)
        evidence.update(validation="PASS", plan=plan.to_dict())
    except ValidationFailure as exc:
        evidence["issues"] = [{"path": issue.path, "code": issue.code} for issue in exc.issues]
        raise
    except ModelProviderError as exc:
        evidence["provider_error"] = exc.code.value
        raise
    finally:
        evidence["elapsed_seconds"] = round(time.monotonic() - started, 3)
        await asyncio.to_thread(write_evidence, evidence)
