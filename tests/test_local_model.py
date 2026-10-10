"""Explicit opt-in real provider lane: pytest -m live. Never falls back or skips."""

import asyncio
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from live_helpers import ObservedTransport, validate_and_record

from agentic_intent_gate.config.configuration import Settings
from agentic_intent_gate.domain.contracts import Intent
from agentic_intent_gate.domain.validation import ValidationFailure
from agentic_intent_gate.infrastructure.llm.local_openai import PROMPT_VERSION, LocalOpenAIProvider
from agentic_intent_gate.ports.model_provider import ModelProviderError

pytestmark = pytest.mark.live


def write_evidence(evidence):
    output = Path(os.environ.get("LIVE_MODEL_EVIDENCE_DIR", ".runtime/live-model"))
    output.mkdir(parents=True, exist_ok=True)
    record = output / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    record.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def test_real_phi4_mini_raw_output_and_validation(validator):
    settings = Settings.from_environment()
    observed = ObservedTransport()
    provider = LocalOpenAIProvider(settings.endpoint, settings.model, transport=observed)
    intent = Intent("Plan to read the service-demo record.")
    evidence = {
        "recorded_at_utc": datetime.now(UTC).isoformat(),
        "lane": "LIVE_OLLAMA_ONE_PROVIDER_CALL_NO_MOCK",
        "prompt_version": PROMPT_VERSION,
        "base_url": settings.endpoint,
        "model": settings.model,
        "wire_method": "POST",
        "wire_path": "/v1/chat/completions",
        "request_parameters": {
            "n": 1,
            "stream": False,
            "temperature": 0,
            "max_tokens": 1024,
            "response_format": {"type": "json_object"},
        },
        "model_calls": 0,
        "json_parse": "NOT_RUN",
        "schema_validation": "NOT_RUN",
        "semantic_validation": "NOT_RUN",
        "trusted_risk_recomputation": "NOT_RUN",
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
        plan = validate_and_record(validator, raw, evidence)
        assert len(observed.requests) == 1
        assert observed.requests[0]["response_format"] == {"type": "json_object"}
        evidence.update(
            validation="PASS",
            safe_summary={
                "risk_class": plan.risk_class.value,
                "steps": len(plan.steps),
                "response_bytes": len(raw.encode("utf-8")),
            },
        )
    except ValidationFailure as exc:
        evidence["issues"] = [{"path": issue.path, "code": issue.code} for issue in exc.issues]
        raise
    except ModelProviderError as exc:
        evidence["provider_error"] = exc.code.value
        raise
    finally:
        evidence["elapsed_seconds"] = round(time.monotonic() - started, 3)
        evidence["model_calls"] = len(observed.requests)
        evidence["upstream_requests"] = observed.requests
        await asyncio.to_thread(write_evidence, evidence)
