"""Run with a clean wheel-installed interpreter, from outside the source repository."""

import argparse
import asyncio
import hashlib
import json
import secrets
import sqlite3
import sys
import tempfile
from contextlib import closing
from importlib.metadata import version
from importlib.resources import files
from pathlib import Path
from uuid import uuid4

import httpx

import agentic_intent_gate
from agentic_intent_gate.api.local_http import create_app
from agentic_intent_gate.application.action_compiler import ActionCompiler, ToolBinding
from agentic_intent_gate.application.action_execution import ActionExecutionGateway
from agentic_intent_gate.application.generate_plan import PlanningService
from agentic_intent_gate.application.runs import RunService
from agentic_intent_gate.domain.approval_binding import BindingCode, BindingError
from agentic_intent_gate.domain.run_validation import contract_validator
from agentic_intent_gate.infrastructure.persistence.sqlite_action_execution import (
    SQLiteExecutionRepository,
)
from agentic_intent_gate.infrastructure.persistence.sqlite_actions import SQLiteActionRepository
from agentic_intent_gate.infrastructure.persistence.sqlite_run_repository import SQLiteRunRepository
from agentic_intent_gate.infrastructure.persistence.sqlite_v2_migration import migrate_v1_to_v2
from agentic_intent_gate.infrastructure.persistence.sqlite_v2_runs import SQLiteV2RunRepository
from agentic_intent_gate.infrastructure.security.local_capability import (
    CapabilityVerifier,
    LocalDemoAuthorization,
)
from agentic_intent_gate.infrastructure.tools.executable_registry import ExecutableRegistry
from agentic_intent_gate.infrastructure.tools.service_demo import (
    DemoNoteExecutor,
    provision_demo_store,
)
from agentic_intent_gate.infrastructure.tools.tool_catalog import build_validator
from agentic_intent_gate.ports.action_repository import AdmissionCommand


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
    runtime_hashes = {}
    runtime_source = source / "src/agentic_intent_gate"
    for path in sorted(runtime_source.rglob("*")):
        if path.is_file() and path.suffix in {".py", ".html"}:
            relative = path.relative_to(runtime_source).as_posix()
            packaged = files("agentic_intent_gate").joinpath(relative).read_bytes()
            assert packaged == path.read_bytes()
            runtime_hashes[relative] = hashlib.sha256(packaged).hexdigest()
    static_page = files("agentic_intent_gate").joinpath("api", "static", "index.html").read_bytes()
    assert static_page == (source / "src/agentic_intent_gate/api/static/index.html").read_bytes()
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
    action_results = {}
    execution_results = {}

    async def probe():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            page = await client.get("/")
            assert page.status_code == 200 and "Planning preview — no tools executed" in page.text
            assert "textContent" in page.text
            for control in ("run-form", "run-details", "approve-run", "reject-run", "refresh-run"):
                assert f'id="{control}"' in page.text
            assert 'requestRun("/api/runs"' in page.text
            assert 'searchParams.get("run_id")' in page.text
            assert "Approved means approval only" in page.text
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
            migrate_v1_to_v2(
                database,
                Path(temporary.name) / "private-v1-backup.sqlite3",
                validator,
                owners_stopped=True,
            )
            v2 = SQLiteV2RunRepository(database, validator)
            compiler = ActionCompiler(
                validator,
                (
                    ToolBinding(
                        "append_service_note",
                        "1",
                        "demo-note-adapter",
                        "demo-binding-v1",
                        "service-demo",
                        str(uuid4()),
                    ),
                ),
            )
            actions = SQLiteActionRepository(v2, compiler)
            try:
                actions.admit(
                    AdmissionCommand(write.json()["run_id"], 5, "package-subject", str(uuid4()))
                )
            except BindingError as exc:
                assert exc.code is BindingCode.LEGACY
            else:
                raise AssertionError("Legacy approval must never admit an Action")
            v2_app = create_app(planning, run_service=RunService(v2, planning))
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=v2_app), base_url="http://127.0.0.1"
            ) as v2_client:
                new = await v2_client.post(
                    "/api/runs", json={"text": "New separately approved proposal"}
                )
                bound = await v2_client.post(
                    new.headers["location"] + "/approval",
                    json={"decision": "APPROVE", "expected_version": 4},
                )
                assert bound.status_code == 200 and provider.calls == 4
                request = AdmissionCommand(new.json()["run_id"], 5, "package-subject", str(uuid4()))
                admitted = actions.admit(request)
                assert admitted.created and admitted.action.version == 1
                assert actions.admit(request).action == admitted.action
                reopened = SQLiteActionRepository(
                    SQLiteV2RunRepository(database, validator), compiler
                )
                assert reopened.get(admitted.action.action_id) == admitted.action
                assert reopened.admit(request).action.effect_key == admitted.action.effect_key
                assert (
                    await v2_client.get(new.headers["location"] + "/actions")
                ).status_code == 404
                assert provider.calls == 4
            action_results.update(
                migration="PASS",
                legacy_denial="PASS",
                bound_approval="PASS",
                admission_replay_reopen="PASS",
                pending_version=1,
                action_http_routes="DEFAULT_RUN_ONLY_COMPOSITION_404",
                tool_calls=0,
            )
            # Explicit installed-wheel Prompt 3 composition, separate from the default 404 API.
            demo_path = Path(temporary.name) / "isolated-demo.sqlite3"
            scope = admitted.action.spec.execution_scope
            provision_demo_store(demo_path, scope)
            executor = DemoNoteExecutor(demo_path, scope, gate_path=database)
            registry = ExecutableRegistry(executor)
            token = secrets.token_hex(32)
            verifier = CapabilityVerifier(
                token,
                subject_id="package-subject",
                binding=executor.binding,
                operations=("read", "admit", "authorize", "execute"),
            )
            policy = LocalDemoAuthorization(verifier, policy_revision="demo-policy-v1")
            execution_repo = SQLiteExecutionRepository(v2, compiler)
            calls = []
            original = executor.execute

            async def counted(*args):
                calls.append(True)
                return await original(*args)

            executor.execute = counted
            execution_app = create_app(
                planning,
                run_service=RunService(v2, planning),
                action_gateway=ActionExecutionGateway(execution_repo, policy, registry),
                capability_verifier=verifier,
            )
            action_url = "/api/actions/" + admitted.action.action_id
            headers = {"Authorization": "Bearer " + token, "Idempotency-Key": str(uuid4())}
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=execution_app), base_url="http://127.0.0.1"
            ) as execution_client:
                action_page = await execution_client.get("/")
                assert action_page.status_code == 200
                assert 'data-action-enabled="true"' in action_page.text
                assert 'data-action-operations="admit,authorize,execute,read"' in action_page.text
                assert 'id="action-capability" type="password"' in action_page.text
                for control in (
                    "action-console",
                    "admit-action",
                    "authorize-action",
                    "execute-action",
                    "refresh-actions",
                    "clear-capability",
                    "action-warning",
                    "action-result",
                ):
                    assert f'id="{control}"' in action_page.text
                if token in action_page.text:
                    raise AssertionError("Installed Action page exposed managed credential")
                assert "textContent" in action_page.text and 'redirect: "error"' in action_page.text
                assert (await execution_client.get(action_url)).status_code == 401
                authorized = await execution_client.post(
                    action_url + "/authorize", headers=headers, json={"expected_action_version": 1}
                )
                assert (
                    authorized.status_code == 200
                    and authorized.json()["action"]["state"] == "Authorized"
                )
                assert len(calls) == 0
                headers["Idempotency-Key"] = str(uuid4())
                finished = await execution_client.post(
                    action_url + "/execute", headers=headers, json={"expected_action_version": 2}
                )
                assert (
                    finished.status_code == 200
                    and finished.json()["action"]["state"] == "Succeeded"
                )
                replay = await execution_client.post(
                    action_url + "/execute", headers=headers, json={"expected_action_version": 2}
                )
                assert replay.status_code == 200 and replay.json()["replayed"] and len(calls) == 1
                reopened = SQLiteExecutionRepository(
                    SQLiteV2RunRepository(database, validator), compiler
                )
                assert reopened.get(admitted.action.action_id).version == 4
                assert reopened.recover(owners_stopped=True) == 0
                with closing(sqlite3.connect(demo_path)) as external:
                    assert external.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 1
                    assert external.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 1
                    receipt = external.execute("SELECT receipt_reference FROM receipts").fetchone()[
                        0
                    ]
                    assert receipt == finished.json()["action"]["result"]["receipt_reference"]
            execution_results.update(
                capability="PASS",
                authorization="PASS",
                execution="PASS",
                replay_reopen="PASS",
                state="Succeeded",
                version=4,
                adapter_calls=1,
                external_notes=1,
                external_receipts=1,
                separate_database=True,
                automatic_retry=False,
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
        "runtime_sha256": runtime_hashes,
        "static_page_http_status": page_status,
        "static_page_sha256": hashlib.sha256(static_page).hexdigest(),
        "run_ui_resource_byte_identical": True,
        "plan_http_status": plan_status,
        "provider_calls": provider.calls,
        "day2": run_results,
        "day3_prompt2": action_results,
        "day3_prompt3": execution_results,
        "day3_prompt4": {
            "installed_action_ui": "PASS",
            "masked_capability_input": "PASS",
            "public_operation_scope_wiring": "PASS",
            "credential_in_html": False,
            "runtime_and_static_resources_byte_identical": True,
            "actual_browser": "NOT_RUN_PACKAGE_PROBE_IS_HTTP_ONLY",
        },
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
