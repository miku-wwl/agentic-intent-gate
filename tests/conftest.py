import json

import pytest

from agentic_intent_gate.adapters.sqlite_run_repository import SQLiteRunRepository
from agentic_intent_gate.adapters.tool_catalog import build_validator

pytest_plugins = ("action_helpers",)


@pytest.fixture
def validator():
    return build_validator()


@pytest.fixture
def run_repository(tmp_path, validator):
    return SQLiteRunRepository(tmp_path / "runs.sqlite3", validator)


@pytest.fixture
def valid_plan():
    return {
        "goal": "Read the service-demo record",
        "target": "service-demo",
        "steps": [
            {
                "tool_name": "get_service_record",
                "tool_version": "1",
                "arguments": {"service_id": "service-demo"},
            }
        ],
        "risk_class": "READ_ONLY",
    }


@pytest.fixture
def provider_factory(valid_plan):
    class ScriptedProvider:
        """Test-only raw-output fixture, never composed by the product entrypoint."""

        def __init__(self, raw=None, error=None):
            self.raw = json.dumps(valid_plan) if raw is None else raw
            self.error = error
            self.calls = 0

        async def generate_plan(self, intent, **kwargs):
            self.calls += 1
            if self.error:
                raise self.error
            return self.raw

    return ScriptedProvider


@pytest.fixture
def envelope(valid_plan):
    def make(content=None, **overrides):
        value = {
            "model": "phi-test",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(valid_plan) if content is None else content,
                    },
                    "finish_reason": "stop",
                }
            ],
        }
        value.update(overrides)
        return value

    return make
