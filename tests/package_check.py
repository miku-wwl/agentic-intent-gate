"""Run with a clean wheel-installed interpreter, from outside the source repository."""

import argparse
import asyncio
import hashlib
import json
import sys
from importlib.metadata import version
from importlib.resources import files
from pathlib import Path

import httpx

import agentic_intent_gate
from agentic_intent_gate.adapters.local_http import create_app
from agentic_intent_gate.adapters.tool_catalog import build_validator
from agentic_intent_gate.application.generate_plan import PlanningService


def verify(source, output):
    package = Path(agentic_intent_gate.__file__).resolve()
    assert not Path.cwd().resolve().is_relative_to(source)
    assert package.is_relative_to(Path(sys.prefix).resolve())
    assert not package.is_relative_to(source)
    schema_hashes = {}
    for path in sorted((source / "schemas").glob("*.json")):
        packaged = files("agentic_intent_gate").joinpath("schemas", path.name).read_bytes()
        assert packaged == path.read_bytes()
        schema_hashes[path.name] = hashlib.sha256(packaged).hexdigest()
    plan = {
        "goal": "Read service-demo from the installed wheel",
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

    class ControlledProvider:
        calls = 0

        async def generate_plan(self, intent, **kwargs):
            self.calls += 1
            return json.dumps(plan)

    provider = ControlledProvider()
    app = create_app(PlanningService(provider, build_validator()))

    async def probe():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            page = await client.get("/")
            assert page.status_code == 200 and "Planning preview — no tools executed" in page.text
            assert "textContent" in page.text
            assert provider.calls == 0
            response = await client.post("/api/plans", json={"text": "Read service-demo"})
            assert response.status_code == 200 and response.json() == {
                "state": "PlanValidated",
                "plan": plan,
                "error": None,
            }
            assert provider.calls == 1
            return page.status_code, response.status_code

    page_status, plan_status = asyncio.run(probe())
    result = {
        "lane": "PACKAGE_CONTROLLED_PROVIDER_NOT_LIVE",
        "result": "PASS",
        "python": sys.version.split()[0],
        "working_directory": str(Path.cwd()),
        "package_import_location": str(package),
        "isolated_environment": str(Path(sys.prefix)),
        "schema_sha256": schema_hashes,
        "static_page_http_status": page_status,
        "plan_http_status": plan_status,
        "provider_calls": provider.calls,
        "versions": {name: version(name) for name in ("fastapi", "httpx", "jsonschema", "uvicorn")},
    }
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print("PACKAGE_REPRODUCIBILITY=PASS")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    verify(args.source.resolve(), args.output.resolve())
