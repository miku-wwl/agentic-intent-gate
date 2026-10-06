"""Run with a clean wheel-installed interpreter, from outside the source repository."""

import argparse
import asyncio
import hashlib
import json
import sys
import tempfile
from importlib.metadata import version
from importlib.resources import files
from pathlib import Path

import httpx

import agentic_intent_gate
from agentic_intent_gate.adapters.local_http import create_app
from agentic_intent_gate.adapters.sqlite_run_repository import SQLiteRunRepository
from agentic_intent_gate.adapters.tool_catalog import build_validator
from agentic_intent_gate.application.generate_plan import PlanningService
from agentic_intent_gate.application.runs import RunService
from agentic_intent_gate.domain.run_validation import contract_validator


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
    validator = build_validator()
    planning = PlanningService(provider, validator)
    temporary = tempfile.TemporaryDirectory(prefix="aig-package-runs-")
    database = Path(temporary.name) / "package.sqlite3"
    repository = SQLiteRunRepository(database, validator)
    app = create_app(planning, run_service=RunService(repository, planning))
    run_results = {}

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
            read = await client.post("/api/runs", json={"text": "Read service-demo"})
            assert read.status_code == 201 and read.json()["state"] == "Ready"
            contract_validator("day2-run.schema.json").validate(read.json())
            assert (await client.get(read.headers["location"])).json() == read.json()
            plan.update(
                risk_class="SIDE_EFFECT",
                steps=[
                    {
                        "tool_name": "append_service_note",
                        "tool_version": "1",
                        "arguments": {"service_id": "service-demo", "note": "Package test"},
                    }
                ],
            )
            write = await client.post("/api/runs", json={"text": "Propose a note"})
            assert write.status_code == 201 and write.json()["state"] == "AwaitingApproval"
            approved = await client.post(
                write.headers["location"] + "/approval",
                json={
                    "decision": "APPROVE",
                    "expected_version": 4,
                },
            )
            assert approved.status_code == 200 and approved.json()["state"] == "Approved"
            assert approved.json()["version"] == 5 and provider.calls == 3
            assert (
                SQLiteRunRepository(database, validator).get(write.json()["run_id"]).to_dict()
                == approved.json()
            )
            run_results.update(
                create_read=201,
                create_write=201,
                approval=200,
                file_reopen="PASS",
                local_schema_resolution="PASS",
            )
            return page.status_code, response.status_code

    try:
        page_status, plan_status = asyncio.run(probe())
    finally:
        temporary.cleanup()
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
        "day2": run_results,
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
