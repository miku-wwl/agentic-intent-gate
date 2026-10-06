"""Deterministic subprocess fixture; parent actually kills paused commit barriers."""

import argparse
import asyncio
import json
import os
import sqlite3
import sys
from pathlib import Path

from run_helpers import database_rows, plan_for

from agentic_intent_gate.adapters import sqlite_run_repository as adapter
from agentic_intent_gate.adapters.sqlite_run_repository import SQLiteRunRepository
from agentic_intent_gate.adapters.tool_catalog import build_validator
from agentic_intent_gate.application.generate_plan import PlanningService
from agentic_intent_gate.application.runs import RunService
from agentic_intent_gate.domain.approval import ApprovalChoice, ApprovalDecisionRequest
from agentic_intent_gate.domain.contracts import Intent, Risk


def emit(value):
    print(json.dumps(value), flush=True)


def pause(run_id, provider, stage):
    emit(
        {
            "run_id": run_id,
            "barrier": stage,
            "provider_calls": provider.calls,
            "tool_calls": 0,
            "pid": os.getpid(),
        }
    )
    sys.stdin.readline()  # Parent terminates this process, never resumes it.
    raise RuntimeError("Crash barrier unexpectedly resumed")


def populate(path, stage, risk, choice):
    validator = build_validator()

    class ControlledProvider:
        calls = 0

        async def generate_plan(self, intent, **kwargs):
            self.calls += 1
            return json.dumps(plan_for(validator, risk).to_dict())

    provider = ControlledProvider()

    class BarrierRepository(SQLiteRunRepository):
        def create(self, *args):
            run = super().create(*args)
            if stage == "received":
                pause(run.run_id, provider, stage)
            return run

        def start_generation(self, *args):
            run = super().start_generation(*args)
            if stage == "generating":
                pause(run.run_id, provider, stage)
            return run

        def save_validated_plan(self, *args):
            run = super().save_validated_plan(*args)
            if stage == "validated":
                pause(run.run_id, provider, stage)
            return run

        def route_validated_plan(self, *args, **kwargs):
            run = super().route_validated_plan(*args, **kwargs)
            if stage == "routed":
                pause(run.run_id, provider, stage)
            return run

    repository = BarrierRepository(path, validator)
    run = asyncio.run(
        RunService(repository, PlanningService(provider, validator)).create(Intent("Plan"))
    )
    if stage == "approval-before":

        class BeforeCommitConnection(sqlite3.Connection):
            def commit(self):
                # All decision/state/audit writes are pending in the real product transaction.
                if self.in_transaction:
                    pause(run.run_id, provider, stage)
                super().commit()

        def connect(db_path):
            return sqlite3.connect(
                db_path, isolation_level=None, timeout=0.25, factory=BeforeCommitConnection
            )

        adapter.open_existing = connect
    repository.decide_approval(run.run_id, ApprovalDecisionRequest(choice, 4, "Local decision"))
    pause(run.run_id, provider, stage)


def reopen_with_product_main(path):
    # Exercise the real configuration, SQLite startup validation and recovery composition.
    from agentic_intent_gate import main as product

    calls = {"provider": 0, "planning": 0, "serve": 0}

    async def forbidden_provider(*args, **kwargs):
        calls["provider"] += 1
        raise AssertionError("Provider called during startup recovery")

    async def forbidden_planning(*args, **kwargs):
        calls["planning"] += 1
        raise AssertionError("Planning called during startup recovery")

    product.LocalOpenAIProvider.generate_plan = forbidden_provider
    product.PlanningService.generate_plan = forbidden_planning

    def inspect_app(app, **kwargs):
        calls["serve"] += 1
        assert [route.path for route in app.routes] == [
            "/",
            "/api/plans",
            "/api/runs",
            "/api/runs/{run_id}",
            "/api/runs/{run_id}/approval",
        ]

    product.uvicorn.run = inspect_app
    os.environ.update(
        FOUNDRY_LOCAL_ENDPOINT="http://127.0.0.1:1",
        FOUNDRY_LOCAL_MODEL="controlled",
        AIG_DB_PATH=str(path),
    )
    product.main()
    repository = SQLiteRunRepository(path, build_validator())
    rows = database_rows(path)
    runs = [repository.get(row[0]).to_dict() for row in rows["runs"]]
    assert calls == {"provider": 0, "planning": 0, "serve": 1}
    emit({"runs": runs, "rows": rows, "calls": calls, "tool_calls": 0, "pid": os.getpid()})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["populate", "reopen"])
    parser.add_argument("path", type=Path)
    parser.add_argument("--stage", default="received")
    parser.add_argument("--risk", default="SIDE_EFFECT")
    parser.add_argument("--choice", default="APPROVE")
    args = parser.parse_args()
    if args.mode == "reopen":
        reopen_with_product_main(args.path)
    else:
        populate(args.path, args.stage, Risk(args.risk), ApprovalChoice(args.choice))
