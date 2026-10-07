"""Gap-driven Prompt 4 integration checks; real disk SQLite, no live model or tools."""

import asyncio
import hashlib
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from run_helpers import advance, database_rows, plan_for

from agentic_intent_gate.adapters import sqlite_run_repository as adapter
from agentic_intent_gate.adapters.local_http import create_app
from agentic_intent_gate.adapters.models.local_openai import ENVELOPE_BYTES, LocalOpenAIProvider
from agentic_intent_gate.adapters.sqlite_run_repository import SQLiteRunRepository
from agentic_intent_gate.adapters.sqlite_schema import configure_connection, open_existing
from agentic_intent_gate.application.generate_plan import PlanningService
from agentic_intent_gate.application.recover_runs import recover_runs
from agentic_intent_gate.application.runs import RunService
from agentic_intent_gate.domain.contracts import ErrorCode, Risk
from agentic_intent_gate.domain.run import RUN_MESSAGES, RunErrorCode, RunState, RunStoreError
from agentic_intent_gate.domain.run_validation import canonical_json

EVIDENCE_DIR = (
    Path(__file__).resolve().parents[1]
    / ".runtime/day2-prompt4/cases"
    / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
)
PRIVATE = "private-hardening-sentinel"


@pytest.fixture
def record_case(request):
    """Write only asserted, non-sensitive observations to ignored evidence artifacts."""

    def record(**observations):
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        name = hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:16]
        (EVIDENCE_DIR / f"{name}.json").write_text(
            json.dumps({"test": request.node.nodeid, "result": "PASS", **observations}, indent=2)
            + "\n",
            encoding="utf-8",
        )

    return record


def app_for(repository, validator, provider):
    planning = PlanningService(provider, validator)
    return create_app(planning, run_service=RunService(repository, planning))


def client_for(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1")


def assert_safe_error(response, run_id):
    assert response.status_code == 503
    assert response.json() == {
        "run_id": run_id,
        "error": {
            "code": "DATABASE_UNAVAILABLE",
            "message": RUN_MESSAGES[RunErrorCode.DATABASE_UNAVAILABLE],
        },
    }


def assert_history(rows, version):
    assert len(rows["runs"]) == 1
    assert len(rows["audit_events"]) == version
    assert [row[2] for row in rows["audit_events"]] == list(range(1, version + 1))


RECOVERY_CASES = [
    (RunState.IntentReceived, Risk.SIDE_EFFECT, RunState.PlanRejected, 2),
    (RunState.PlanGenerating, Risk.SIDE_EFFECT, RunState.PlanRejected, 3),
    *[
        (
            RunState.PlanValidated,
            risk,
            RunState.Ready if risk == Risk.READ_ONLY else RunState.AwaitingApproval,
            4,
        )
        for risk in Risk
    ],
    (RunState.Ready, Risk.READ_ONLY, RunState.Ready, 4),
    (RunState.AwaitingApproval, Risk.SIDE_EFFECT, RunState.AwaitingApproval, 4),
    (RunState.Approved, Risk.SIDE_EFFECT, RunState.Approved, 5),
    (RunState.Rejected, Risk.SIDE_EFFECT, RunState.Rejected, 5),
    (RunState.PlanRejected, Risk.READ_ONLY, RunState.PlanRejected, 2),
]


@pytest.mark.parametrize("state,risk,expected,version", RECOVERY_CASES)
def test_three_recovery_passes_converge_all_states(
    run_repository, validator, monkeypatch, record_case, state, risk, expected, version
):
    run = advance(run_repository, state, risk)
    before = database_rows(run_repository.path)
    calls = {"provider": 0, "planning": 0}

    async def forbidden_provider(*args, **kwargs):
        calls["provider"] += 1
        raise AssertionError("Recovery must not call provider")

    async def forbidden_planning(*args, **kwargs):
        calls["planning"] += 1
        raise AssertionError("Recovery must not invoke planning")

    monkeypatch.setattr(LocalOpenAIProvider, "generate_plan", forbidden_provider)
    monkeypatch.setattr(PlanningService, "generate_plan", forbidden_planning)
    first_rows = first_run = None
    for _ in range(3):
        reopened = SQLiteRunRepository(run_repository.path, validator)
        recover_runs(reopened)
        recovered = reopened.get(run.run_id)
        rows = database_rows(run_repository.path)
        assert recovered.state == expected and recovered.version == version
        assert recovered.plan == run.plan
        assert rows["approval_decisions"] == before["approval_decisions"]
        assert_history(rows, version)
        if first_rows is None:
            first_rows, first_run = rows, recovered
        else:
            assert rows == first_rows and recovered == first_run
    if state in {RunState.IntentReceived, RunState.PlanGenerating, RunState.PlanValidated}:
        assert len(first_rows["audit_events"]) == len(before["audit_events"]) + 1
        assert first_rows["audit_events"][-1][3] == (
            "RECOVERY_ROUTED" if state == RunState.PlanValidated else "RECOVERY_REJECTED"
        )
    else:
        assert first_rows == before
    assert calls == {"provider": 0, "planning": 0}
    record_case(
        initial_state=state.value,
        risk=risk.value,
        final_state=expected.value,
        version=version,
        recovery_passes=3,
        audit_count=version,
        provider_calls=0,
        repeated_rows_unchanged=True,
    )


@pytest.mark.parametrize(
    "choices", [("APPROVE", "APPROVE"), ("REJECT", "REJECT"), ("APPROVE", "REJECT")]
)
def test_two_http_requests_race_one_durable_decision(
    run_repository, validator, provider_factory, monkeypatch, record_case, choices
):
    run = advance(run_repository, RunState.AwaitingApproval, Risk.SIDE_EFFECT)
    provider = provider_factory()
    app = app_for(run_repository, validator, provider)
    barrier = threading.Barrier(2)
    connections = []

    class RacingConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql == "BEGIN IMMEDIATE":
                barrier.wait(timeout=5)
            return super().execute(sql, parameters)

    def connect(path):
        connection = sqlite3.connect(
            path.as_uri() + "?mode=rw",
            uri=True,
            isolation_level=None,
            timeout=0.25,
            factory=RacingConnection,
        )
        connections.append(connection)
        return connection

    async def request(choice):
        async with client_for(app) as client:
            return await client.post(
                f"/api/runs/{run.run_id}/approval",
                json={"decision": choice, "expected_version": 4},
            )

    with monkeypatch.context() as patch:
        patch.setattr(adapter, "open_existing", connect)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda choice: asyncio.run(request(choice)), choices))
    assert len(connections) == 2 and connections[0] is not connections[1]
    assert sorted(r.status_code for r in results) == [200, 409]
    winner = next(r.json() for r in results if r.status_code == 200)
    loser = next(r.json() for r in results if r.status_code == 409)
    assert loser["error"]["code"] == "STALE_RUN_VERSION"
    final = run_repository.get(run.run_id)
    rows = database_rows(run_repository.path)
    assert final.to_dict() == winner and final.version == 5
    assert len(rows["approval_decisions"]) == 1
    assert rows["approval_decisions"][0][2] == (
        "APPROVE" if final.state == RunState.Approved else "REJECT"
    )
    assert_history(rows, 5)
    assert sum(row[3] in {"APPROVED", "REJECTED"} for row in rows["audit_events"]) == 1
    assert provider.calls == 0
    record_case(
        choices=choices,
        statuses=[r.status_code for r in results],
        final_state=final.state.value,
        version=5,
        decision_rows=1,
        successful_audit_events=1,
        provider_calls=0,
        transport="in-process ASGI; two threads and independent real SQLite connections",
    )


@pytest.mark.parametrize("concurrent", [False, True])
async def test_same_intent_creates_two_independent_runs(
    run_repository, validator, provider_factory, record_case, concurrent
):
    provider = provider_factory()
    app = app_for(run_repository, validator, provider)
    entered = 0
    if concurrent:
        both_entered = asyncio.Event()
        original_generate = provider.generate_plan

        async def overlapping_generate(*args, **kwargs):
            nonlocal entered
            entered += 1
            if entered == 2:
                both_entered.set()
            await asyncio.wait_for(both_entered.wait(), timeout=5)
            return await original_generate(*args, **kwargs)

        provider.generate_plan = overlapping_generate
    async with client_for(app) as client:

        async def create():
            return await client.post("/api/runs", json={"text": "Same Intent"})

        results = (
            await asyncio.gather(create(), create())
            if concurrent
            else [await create(), await create()]
        )
    assert [r.status_code for r in results] == [201, 201]
    ids = [r.json()["run_id"] for r in results]
    assert len(set(ids)) == 2 and provider.calls == 2
    rows = database_rows(run_repository.path)
    assert len(rows["runs"]) == 2 and not rows["approval_decisions"]
    for run_id in ids:
        run = run_repository.get(run_id)
        assert run.state == RunState.Ready and run.version == 4
        assert run.intent.text == "Same Intent"
        assert [r[2] for r in rows["audit_events"] if r[1] == run_id] == [1, 2, 3, 4]
    assert entered == (2 if concurrent else 0)
    record_case(
        concurrent=concurrent,
        run_count=2,
        provider_calls=2,
        calls_per_run=1,
        overlapping_provider_entries=entered,
    )


@pytest.mark.parametrize(
    "version,status", [(3, 409), (6, 409), ("4", 400), (4.0, 400), (True, 400), (None, 400)]
)
async def test_waiting_run_old_new_and_malformed_version_are_pure(
    run_repository, validator, provider_factory, record_case, version, status
):
    run = advance(run_repository, RunState.AwaitingApproval, Risk.SIDE_EFFECT)
    rows = database_rows(run_repository.path)
    provider = provider_factory()
    async with client_for(app_for(run_repository, validator, provider)) as client:
        response = await client.post(
            f"/api/runs/{run.run_id}/approval",
            json={"decision": "APPROVE", "expected_version": version},
        )
    assert response.status_code == status
    assert response.json()["error"]["code"] == (
        "STALE_RUN_VERSION" if status == 409 else "INVALID_APPROVAL"
    )
    assert database_rows(run_repository.path) == rows and provider.calls == 0
    record_case(expected_version=version, status=status, rows_unchanged=True, provider_calls=0)


@pytest.mark.parametrize("operation", ["create", "approve", "reject"])
async def test_actual_locked_http_failure_is_bounded_and_safe(
    run_repository, validator, provider_factory, record_case, operation
):
    run = advance(run_repository, RunState.AwaitingApproval, Risk.SIDE_EFFECT)
    before = database_rows(run_repository.path)
    provider = provider_factory()
    with closing(open_existing(run_repository.path)) as blocker:
        configure_connection(blocker)
        blocker.execute("BEGIN IMMEDIATE")
        started = time.monotonic()
        try:
            async with client_for(app_for(run_repository, validator, provider)) as client:
                response = await client.post(
                    "/api/runs" if operation == "create" else f"/api/runs/{run.run_id}/approval",
                    json={"text": PRIVATE}
                    if operation == "create"
                    else {"decision": operation.upper(), "expected_version": 4},
                )
            elapsed = time.monotonic() - started
            response_id = response.json()["run_id"]
            assert_safe_error(response, response_id)
            assert response_id is not None
            if operation != "create":
                assert response_id == run.run_id
            assert elapsed < 1.5 and provider.calls == 0
            assert run_repository.get(run.run_id) == run
        finally:
            blocker.rollback()
    assert database_rows(run_repository.path) == before
    assert str(run_repository.path) not in response.text and PRIVATE not in response.text
    record_case(
        operation=operation,
        elapsed_seconds=elapsed,
        status=503,
        provider_calls=0,
        rows_unchanged=True,
    )


CREATION_PHASES = {
    "create": ("IntentReceived", "RUN_CREATED", None, 0),
    "start": ("PlanGenerating", "PLAN_GENERATION_STARTED", "IntentReceived", 1),
    "save": ("PlanValidated", "PLAN_VALIDATED", "PlanGenerating", 2),
    "route": ("AwaitingApproval", "APPROVAL_REQUIRED", "PlanValidated", 3),
}


@pytest.mark.parametrize("phase", list(CREATION_PHASES))
@pytest.mark.parametrize("boundary", ["write", "audit", "commit"])
async def test_creation_sql_failure_preserves_recoverable_barrier(
    run_repository, validator, provider_factory, monkeypatch, record_case, phase, boundary
):
    target, event, previous_state, previous_version = CREATION_PHASES[phase]
    provider = provider_factory(raw=json.dumps(plan_for(validator, Risk.SIDE_EFFECT).to_dict()))
    app = app_for(run_repository, validator, provider)
    injections = []

    class FaultConnection(sqlite3.Connection):
        armed = False

        def execute(self, sql, parameters=()):
            result = super().execute(sql, parameters)
            target_write = (phase == "create" and sql.startswith("INSERT INTO runs")) or (
                sql.startswith("UPDATE runs") and parameters[0] == target
            )
            if target_write:
                self.armed = True
            target_audit = sql.startswith("INSERT INTO audit_events") and parameters[2] == event
            if (boundary == "write" and target_write) or (boundary == "audit" and target_audit):
                injections.append(boundary)
                raise sqlite3.OperationalError(PRIVATE)
            return result

        def commit(self):
            if boundary == "commit" and self.armed:
                injections.append(boundary)
                raise sqlite3.OperationalError(PRIVATE)
            return super().commit()

    def connect(path):
        return sqlite3.connect(
            path.as_uri() + "?mode=rw",
            uri=True,
            isolation_level=None,
            factory=FaultConnection,
            timeout=0.25,
        )

    with monkeypatch.context() as patch:
        patch.setattr(adapter, "open_existing", connect)
        async with client_for(app) as client:
            response = await client.post("/api/runs", json={"text": "Controlled creation"})
    run_id = response.json()["run_id"]
    assert run_id is not None
    assert_safe_error(response, run_id)
    assert injections == [boundary]
    assert provider.calls == (0 if phase in {"create", "start"} else 1)
    rows = database_rows(run_repository.path)
    assert not rows["approval_decisions"]
    if previous_state is None:
        assert not rows["runs"] and not rows["audit_events"]
    else:
        saved = run_repository.get(run_id)
        assert saved.state.value == previous_state and saved.version == previous_version
        assert (saved.plan is not None) == (phase == "route")
        assert_history(rows, previous_version)
    assert PRIVATE not in response.text and PRIVATE not in repr(rows)
    assert str(run_repository.path) not in response.text
    reopened = SQLiteRunRepository(run_repository.path, validator)
    recover_runs(reopened)
    recovered = reopened.get(run_id)
    if phase == "create":
        assert recovered is None
    else:
        assert recovered.state == (
            RunState.AwaitingApproval if phase == "route" else RunState.PlanRejected
        )
        assert recovered.version == previous_version + 1
        assert_history(database_rows(run_repository.path), recovered.version)
    assert provider.calls == (0 if phase in {"create", "start"} else 1)
    record_case(
        phase=phase,
        boundary=boundary,
        previous_state=previous_state,
        previous_version=previous_version,
        recovered_state=recovered.state.value if recovered else None,
        recovered_version=recovered.version if recovered else None,
        provider_calls=provider.calls,
        injected_failures=len(injections),
        status=503,
    )


@pytest.mark.parametrize(
    "corruption",
    [
        "missing-steps",
        "unknown-tool",
        "bad-argument",
        "bad-revision",
        "write-claimed-read",
        "read-claimed-write",
    ],
)
async def test_persisted_plan_attacks_fail_closed_without_repair(
    run_repository, validator, provider_factory, record_case, corruption
):
    risk = (
        Risk.APPROVAL_REQUIRED
        if corruption == "bad-revision"
        else (Risk.READ_ONLY if corruption == "read-claimed-write" else Risk.SIDE_EFFECT)
    )
    run = advance(run_repository, RunState.PlanValidated, risk)
    data = run.plan.to_dict()
    if corruption == "missing-steps":
        del data["steps"]
    elif corruption == "unknown-tool":
        data["steps"][0]["tool_name"] = PRIVATE
    elif corruption == "bad-argument":
        data["steps"][0]["arguments"]["note"] = {"private": PRIVATE}
    elif corruption == "bad-revision":
        data["steps"][0]["arguments"]["expected_revision"] = 2
    else:
        data["risk_class"] = "SIDE_EFFECT" if risk == Risk.READ_ONLY else "READ_ONLY"
    with closing(sqlite3.connect(run_repository.path)) as connection, connection:
        connection.execute(
            "UPDATE runs SET validated_plan_json=?, risk_class=? WHERE run_id=?",
            (canonical_json(data), data["risk_class"], run.run_id),
        )
    corrupted = database_rows(run_repository.path)
    provider = provider_factory()
    async with client_for(app_for(run_repository, validator, provider)) as client:
        response = await client.get(f"/api/runs/{run.run_id}")
    assert_safe_error(response, run.run_id)
    with pytest.raises(RunStoreError) as caught:
        SQLiteRunRepository(run_repository.path, validator)
    assert caught.value.code == RunErrorCode.DATABASE_UNAVAILABLE
    assert database_rows(run_repository.path) == corrupted and provider.calls == 0
    assert PRIVATE not in response.text and str(run_repository.path) not in response.text
    record_case(
        corruption=corruption, status=503, rows_unchanged=True, provider_calls=0, repaired=False
    )


@pytest.mark.parametrize("failure", ["invalid-header", "blocked-parent"])
def test_database_failure_preserves_forensic_bytes(tmp_path, validator, record_case, failure):
    sentinel = tmp_path / "forensic-evidence"
    content = b"not a SQLite database: private controlled forensic bytes"
    sentinel.write_bytes(content)
    path = sentinel if failure == "invalid-header" else sentinel / "runs.sqlite3"
    started = time.monotonic()
    with pytest.raises(RunStoreError) as caught:
        SQLiteRunRepository(path, validator)
    assert caught.value.code == RunErrorCode.DATABASE_UNAVAILABLE
    assert time.monotonic() - started < 1.5
    assert sentinel.read_bytes() == content
    assert str(path) not in str(caught.value) and PRIVATE not in str(caught.value)
    assert not list(tmp_path.glob("*-wal")) and not list(tmp_path.glob("*-shm"))
    record_case(failure=failure, forensic_bytes_unchanged=True, auto_reset=False)


PROVIDER_FAILURES = {
    "timeout": ErrorCode.MODEL_TIMEOUT,
    "connection": ErrorCode.MODEL_UNAVAILABLE,
    "invalid-envelope": ErrorCode.MODEL_RESPONSE_ERROR,
    "missing-content": ErrorCode.MODEL_RESPONSE_ERROR,
    "oversized-envelope": ErrorCode.MODEL_RESPONSE_ERROR,
    "invalid-json": ErrorCode.PLAN_VALIDATION_ERROR,
    "schema-invalid": ErrorCode.PLAN_VALIDATION_ERROR,
    "semantic-invalid": ErrorCode.PLAN_VALIDATION_ERROR,
}


@pytest.mark.parametrize("failure,code", list(PROVIDER_FAILURES.items()))
async def test_controlled_provider_fault_becomes_one_durable_rejection(
    run_repository, validator, valid_plan, envelope, record_case, failure, code
):
    requests = []
    data = dict(valid_plan)
    data["goal"] = PRIVATE
    if failure == "schema-invalid":
        del data["steps"]
    elif failure == "semantic-invalid":
        data["target"] = PRIVATE

    def respond(request):
        requests.append(request)
        payload = json.loads(request.content)
        assert payload["response_format"] == {"type": "json_object"}
        assert payload["n"] == 1 and payload["stream"] is False
        if failure == "timeout":
            raise httpx.ReadTimeout(PRIVATE, request=request)
        if failure == "connection":
            raise httpx.ConnectError(PRIVATE, request=request)
        if failure == "invalid-envelope":
            return httpx.Response(200, content=PRIVATE)
        if failure == "oversized-envelope":
            return httpx.Response(200, content=b"x" * (ENVELOPE_BYTES + 1))
        value = envelope(
            PRIVATE if failure == "invalid-json" else json.dumps(data), model="controlled"
        )
        if failure == "missing-content":
            del value["choices"][0]["message"]["content"]
        return httpx.Response(200, json=value)

    class CountingProvider(LocalOpenAIProvider):
        calls = 0

        async def generate_plan(self, *args, **kwargs):
            self.calls += 1
            return await super().generate_plan(*args, **kwargs)

    provider = CountingProvider(
        "http://127.0.0.1:1/v1", "controlled", transport=httpx.MockTransport(respond)
    )
    async with client_for(app_for(run_repository, validator, provider)) as client:
        response = await client.post("/api/runs", json={"text": "Controlled failure"})
        assert response.status_code == 201
        run = response.json()
        assert run["state"] == "PlanRejected" and run["version"] == 3
        assert run["plan"] is None and run["risk_class"] is None
        assert run["error"] == {"code": code.value, "message": RUN_MESSAGES[code]}
        before = database_rows(run_repository.path)
        for _ in range(3):
            get = await client.get(response.headers["location"])
            assert get.status_code == 200 and get.json() == run
    assert database_rows(run_repository.path) == before
    assert provider.calls == len(requests) == 1
    assert_history(before, 3)
    assert not before["approval_decisions"]
    assert PRIVATE not in response.text and PRIVATE not in repr(before)
    assert "127.0.0.1:1" not in response.text and str(run_repository.path) not in response.text
    record_case(
        failure=failure,
        error_code=code.value,
        provider_calls=1,
        transport_calls=1,
        final_state="PlanRejected",
        version=3,
        audit_count=3,
        repeated_get_unchanged=True,
    )
