"""Real process termination and fresh product-main startup against the same disk DB."""

import json
import os
import queue
import subprocess
import sys
import threading
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest
from run_helpers import advance, database_rows

from agentic_intent_gate.adapters.sqlite_run_repository import SQLiteRunRepository
from agentic_intent_gate.application.recover_runs import recover_runs
from agentic_intent_gate.domain.contracts import Risk
from agentic_intent_gate.domain.run import RunState

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "tests/restart_worker.py"
EVIDENCE_DIR = (
    ROOT / ".runtime/day2-prompt2/restarts" / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
)
SCENARIOS = [
    ("received", "SIDE_EFFECT", "APPROVE"),
    ("generating", "SIDE_EFFECT", "APPROVE"),
    ("validated", "READ_ONLY", "APPROVE"),
    ("validated", "SIDE_EFFECT", "APPROVE"),
    ("validated", "APPROVAL_REQUIRED", "APPROVE"),
    ("routed", "READ_ONLY", "APPROVE"),
    ("routed", "SIDE_EFFECT", "APPROVE"),
    ("routed", "APPROVAL_REQUIRED", "APPROVE"),
    ("approval-before", "SIDE_EFFECT", "APPROVE"),
    ("approval-before", "SIDE_EFFECT", "REJECT"),
    ("approval-after", "SIDE_EFFECT", "APPROVE"),
    ("approval-after", "SIDE_EFFECT", "REJECT"),
    ("plan-rejected", "SIDE_EFFECT", "APPROVE"),
]


def child_command(*args):
    # Windows venv redirectors launch a second process; use the base interpreter directly.
    executable = sys._base_executable if os.name == "nt" else sys.executable
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        [
            str(ROOT / "src"),
            str(ROOT / "tests"),
            str(
                Path(sys.prefix)
                / ("Lib/site-packages" if os.name == "nt" else "lib/python3.13/site-packages")
            ),
        ]
    )
    return [executable, str(WORKER), *map(str, args)], environment


def fresh_reopen(path, cwd):
    command, environment = child_command("reopen", path)
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert (
        result["calls"] == {"provider": 0, "planning": 0, "serve": 1} and result["tool_calls"] == 0
    )
    return result


@pytest.mark.parametrize("stage,risk,choice", SCENARIOS)
def test_real_kill_and_fresh_process_recovery(tmp_path, stage, risk, choice):
    path = tmp_path / "restart.sqlite3"
    command, environment = child_command(
        "populate", path, "--stage", stage, "--risk", risk, "--choice", choice
    )
    process = subprocess.Popen(
        command,
        cwd=tmp_path,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    messages = queue.Queue()
    reader = threading.Thread(target=lambda: messages.put(process.stdout.readline()))
    reader.start()
    try:
        line = messages.get(timeout=15)
        if not line:
            raise AssertionError(process.stderr.read())
        handshake = json.loads(line)
        assert handshake["pid"] == process.pid and handshake["barrier"] == stage
        assert handshake["provider_calls"] == (0 if stage in {"received", "generating"} else 1)
        assert handshake["tool_calls"] == 0
        process.kill()  # Abrupt termination at the commit barrier.
        process.wait(timeout=10)
        assert process.returncode != 0
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
        reader.join(timeout=2)
        process.stdin.close()
        process.stdout.close()
        process.stderr.close()
        assert not reader.is_alive()
    before = database_rows(path)
    expected_pre = {
        "received": "IntentReceived",
        "generating": "PlanGenerating",
        "validated": "PlanValidated",
        "routed": "Ready" if risk == "READ_ONLY" else "AwaitingApproval",
        "approval-before": "AwaitingApproval",
        "approval-after": "Approved" if choice == "APPROVE" else "Rejected",
        "plan-rejected": "PlanRejected",
    }[stage]
    assert before["runs"][0][2] == expected_pre
    first = fresh_reopen(path, tmp_path)
    assert first["pid"] != process.pid
    run = first["runs"][0]
    assert run["run_id"] == handshake["run_id"]
    if stage in {"received", "generating"}:
        assert run["state"] == "PlanRejected" and run["plan"] is None
        assert run["version"] == (2 if stage == "received" else 3)
        assert run["error"] == {
            "code": "RECOVERY_INTERRUPTED",
            "message": "Planning was interrupted before a validated Plan was stored.",
        }
        assert first["rows"]["audit_events"][-1][3] == "RECOVERY_REJECTED"
    elif stage == "validated":
        assert run["state"] == ("Ready" if risk == "READ_ONLY" else "AwaitingApproval")
        assert run["version"] == 4 and run["plan"] is not None
        assert first["rows"]["audit_events"][-1][3] == "RECOVERY_ROUTED"
    else:
        assert run["state"] == expected_pre
        assert first["rows"] == {key: [list(row) for row in value] for key, value in before.items()}
    decisions = first["rows"]["approval_decisions"]
    assert len(decisions) == (1 if stage == "approval-after" else 0)
    assert len(first["rows"]["audit_events"]) == run["version"]
    second = fresh_reopen(path, tmp_path)
    assert second["rows"] == first["rows"] and second["runs"] == first["runs"]
    third = fresh_reopen(path, tmp_path)
    assert third["rows"] == first["rows"] and third["runs"] == first["runs"]
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    (EVIDENCE_DIR / f"{stage}-{risk}-{choice}.json").write_text(
        json.dumps(
            {
                "scenario": {"barrier": stage, "risk": risk, "decision": choice},
                "terminated_pid": process.pid,
                "exit_code": process.returncode,
                "initial_provider_calls": handshake["provider_calls"],
                "reopen_pids": [first["pid"], second["pid"], third["pid"]],
                "pre_restart_state": expected_pre,
                "recovered_run": run,
                "recovery_calls": first["calls"],
                "repeated_recovery_calls": second["calls"],
                "third_recovery_calls": third["calls"],
                "recovery_passes": 3,
                "recovery_tool_calls": sum(r["tool_calls"] for r in (first, second, third)),
                "audit_count": len(first["rows"]["audit_events"]),
                "decision_count": len(decisions),
                "repeat_unchanged": True,
                "processes_stopped": True,
                "result": "PASS",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def test_recovery_run_id_order_and_unchanged_waiting_terminal_states(
    run_repository, validator, monkeypatch
):
    preserved = []
    for state in (
        RunState.Ready,
        RunState.AwaitingApproval,
        RunState.Approved,
        RunState.Rejected,
        RunState.PlanRejected,
    ):
        risk = (
            Risk.READ_ONLY if state in {RunState.Ready, RunState.PlanRejected} else Risk.SIDE_EFFECT
        )
        preserved.append(advance(run_repository, state, risk))
    candidates = [
        advance(run_repository, state)
        for state in [RunState.PlanValidated, RunState.IntentReceived, RunState.PlanGenerating]
    ]
    assert [r.run_id for r in run_repository.recovery_candidates()] == sorted(
        r.run_id for r in candidates
    )
    order = []
    reject = run_repository.recover_interrupted
    route = run_repository.route_validated_plan

    def recording_reject(run_id, version):
        order.append(run_id)
        return reject(run_id, version)

    def recording_route(run_id, version, **kwargs):
        order.append(run_id)
        return route(run_id, version, **kwargs)

    monkeypatch.setattr(run_repository, "recover_interrupted", recording_reject)
    monkeypatch.setattr(run_repository, "route_validated_plan", recording_route)
    recover_runs(run_repository)
    assert order == sorted(run.run_id for run in candidates)
    assert all(run_repository.get(run.run_id) == run for run in preserved)
    rows = database_rows(run_repository.path)
    recover_runs(SQLiteRunRepository(run_repository.path, validator))
    assert database_rows(run_repository.path) == rows


@pytest.mark.parametrize("fault", ["schema", "data", "recovery"])
def test_production_startup_fails_before_any_route_is_served(
    run_repository, monkeypatch, caplog, fault
):
    import sqlite3

    from agentic_intent_gate import main as product
    from agentic_intent_gate.domain.run import RunErrorCode, RunStoreError

    run = advance(run_repository, RunState.IntentReceived)
    if fault in {"schema", "data"}:
        with closing(sqlite3.connect(run_repository.path)) as connection, connection:
            connection.execute(
                "PRAGMA user_version=2" if fault == "schema" else "DELETE FROM audit_events"
            )
    else:

        def fail_recovery(repository):
            raise RunStoreError(RunErrorCode.DATABASE_UNAVAILABLE, run.run_id)

        monkeypatch.setattr(product, "recover_runs", fail_recovery)
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("LOCAL_LLM_MODEL", "controlled")
    monkeypatch.setenv("AIG_DB_PATH", str(run_repository.path))
    served = []
    monkeypatch.setattr(product.uvicorn, "run", lambda *args, **kwargs: served.append(args))
    with pytest.raises(SystemExit) as caught:
        product.main()
    assert caught.value.code == 2 and served == []
    assert str(run_repository.path) not in caplog.text
    message = (
        "The Run database schema is not supported."
        if fault == "schema"
        else "The Run store is unavailable."
    )
    assert message in caplog.text
