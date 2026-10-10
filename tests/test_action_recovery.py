import sqlite3
from contextlib import closing
from uuid import uuid4

import httpx
import pytest
from action_helpers import cmd
from test_service_demo_adapter import downstream, invoke

from agentic_intent_gate.application.action_execution import ActionExecutionGateway
from agentic_intent_gate.application.generate_plan import PlanningService
from agentic_intent_gate.application.recover_actions import recover_actions
from agentic_intent_gate.config.configuration import Settings
from agentic_intent_gate.domain.action import ActionError, ActionState
from agentic_intent_gate.execution_main import compose
from agentic_intent_gate.infrastructure.persistence.sqlite_action_execution import (
    SQLiteExecutionRepository,
)
from agentic_intent_gate.infrastructure.persistence.sqlite_v2_runs import SQLiteV2RunRepository
from agentic_intent_gate.infrastructure.security.local_capability import provision_capability
from agentic_intent_gate.infrastructure.security.single_owner import single_owner
from agentic_intent_gate.ports.tool_executor import Outcome


@pytest.mark.parametrize("window", ["A", "B", "C", "D", "E"])
async def test_focused_crash_windows_and_three_pure_recovery_passes(
    execution_env, window, monkeypatch
):
    e = execution_env
    request = cmd(e.action)
    if window == "E":
        await e.gateway.execute(request, e.principal)
        calls = e.calls
    else:
        claim = e.repo.claim(request, e.principal, e.policy)
        if window == "B":
            # Controlled dispatched fault; process/power-loss matrix is explicitly Prompt 5.
            original = e.executor.execute

            async def interrupted(*args):
                e.calls += 1
                raise RuntimeError("interrupted after entry, outcome not recorded")

            monkeypatch.setattr(e.executor, "execute", interrupted)
            with pytest.raises(RuntimeError):
                await invoke(e, attempt_id=claim.attempt_id)
            monkeypatch.setattr(e.executor, "execute", original)
        elif window == "C":
            assert (await invoke(e, attempt_id=claim.attempt_id)).category is Outcome.SUCCESS
        elif window == "D":
            with closing(sqlite3.connect(e.demo_path)) as connection:
                connection.execute("UPDATE resource SET execution_scope=?", (str(uuid4()),))
                connection.commit()
            assert (
                await invoke(e, attempt_id=claim.attempt_id)
            ).category is Outcome.CONFIRMED_FAILURE
        calls = e.calls
    effect_before = downstream(e.demo_path)

    def no_call(*args):
        raise AssertionError("Startup must not inspect or call the downstream provider")

    monkeypatch.setattr(e.executor, "execute", no_call)
    reopened_runs = SQLiteV2RunRepository(e.runs.path, e.runs.validator)
    reopened = SQLiteExecutionRepository(reopened_runs, e.repo.compiler)
    assert recover_actions(reopened, predecessor_stopped=True) == (0 if window == "E" else 1)
    result = reopened.get(e.action.action_id)
    assert result.state is (ActionState.Succeeded if window == "E" else ActionState.Unknown)
    assert result.version == 4
    before = e.ledger()
    for _ in range(3):
        assert recover_actions(reopened, predecessor_stopped=True) == 0
        assert e.ledger() == before
    assert downstream(e.demo_path) == effect_before and e.calls == calls
    gateway = ActionExecutionGateway(reopened, e.policy, e.registry)
    assert (await gateway.execute(request, e.principal)).replayed
    with pytest.raises(ActionError):
        await gateway.execute(cmd(result), e.principal)
    assert e.calls == calls
    if window != "E":
        with pytest.raises(ActionError):
            reopened.finish(
                e.action.action_id,
                claim.attempt_id,
                {
                    "code": "EXECUTION_SUCCEEDED",
                    "message": "The demo note effect was confirmed.",
                    "receipt_reference": str(uuid4()),
                },
            )
        assert e.ledger() == before
    assert len(effect_before["notes"]) == (1 if window in {"C", "E"} else 0)


@pytest.mark.parametrize("state", ["Pending", "Authorized", "Succeeded", "Failed", "Unknown"])
async def test_other_five_states_unchanged(demo_env, state):
    e = demo_env
    if state != "Pending":
        e.action = e.repo.authorize(cmd(e.action), e.principal, e.policy).action
    if state in {"Succeeded", "Failed", "Unknown"}:
        if state == "Failed":
            with closing(sqlite3.connect(e.demo_path)) as connection:
                connection.execute("UPDATE resource SET execution_scope=?", (str(uuid4()),))
                connection.commit()
        if state == "Unknown":
            e.repo.claim(cmd(e.action), e.principal, e.policy)
            e.repo.recover(owners_stopped=True)
        else:
            await ActionExecutionGateway(e.repo, e.policy, e.registry).execute(
                cmd(e.action), e.principal
            )
    before, effects = e.ledger(), downstream(e.demo_path)
    assert e.repo.get(e.action.action_id).state.value == state
    assert recover_actions(e.repo, predecessor_stopped=True) == 0
    assert e.ledger() == before and downstream(e.demo_path) == effects


def test_recovery_requires_quiescence_attestation_and_os_owner(execution_env):
    e = execution_env
    before = e.ledger()
    with pytest.raises(ActionError):
        recover_actions(e.repo)
    with pytest.raises(ActionError):
        with single_owner(e.runs.path):
            pass
    with single_owner(e.runs.path, predecessor_stopped=True):
        with pytest.raises(ActionError):
            with single_owner(e.runs.path, predecessor_stopped=True):
                pass
    with single_owner(e.runs.path, predecessor_stopped=True):
        pass
    assert e.ledger() == before


async def test_explicit_product_composition_fails_closed_and_recovers_before_serving(
    execution_env, private_capability_path
):
    e = execution_env

    class ForbiddenProvider:
        async def generate_plan(self, *args, **kwargs):
            raise AssertionError("No startup model calls")

    planning = PlanningService(ForbiddenProvider(), e.runs.validator)
    settings = Settings("http://127.0.0.1:11434/v1", "phi-test", db_path=e.runs.path)
    private = private_capability_path
    provision_capability(private)
    environment = {
        "AIG_CAPABILITY_FILE": str(private),
        "AIG_DEMO_DB_PATH": str(e.demo_path),
        "AIG_EXECUTION_SCOPE": e.action.spec.execution_scope,
        "AIG_SUBJECT_ID": e.principal.subject_id,
        "AIG_POLICY_REVISION": e.policy.policy_revision,
    }
    before = e.ledger()
    with pytest.raises(ActionError):
        compose(settings, environment, planning)
    assert before == e.ledger()
    e.repo.claim(cmd(e.action), e.principal, e.policy)
    with single_owner(e.runs.path, predecessor_stopped=True):
        app = compose(settings, environment, planning, predecessor_stopped=True)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            response = await client.get(
                f"/api/actions/{e.action.action_id}",
                headers={"Authorization": "Bearer " + private.read_text()},
            )
            assert response.status_code == 200 and response.json()["state"] == "Unknown"
    assert e.calls == 0 and not downstream(e.demo_path)["notes"]
