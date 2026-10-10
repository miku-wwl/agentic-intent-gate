"""Isolated random authority and real separate disk persistence; never product fixtures."""

import asyncio
import sqlite3
from contextlib import closing
from threading import Lock
from types import SimpleNamespace
from uuid import uuid4

import pytest
from run_helpers import advance
from test_action_capability import capability
from test_action_persistence import ledger

import agentic_intent_gate.infrastructure.persistence.sqlite_actions as sqlite_actions
from agentic_intent_gate.application.action_compiler import ActionCompiler
from agentic_intent_gate.application.action_execution import ActionExecutionGateway
from agentic_intent_gate.domain.contracts import Risk
from agentic_intent_gate.domain.run import RunState
from agentic_intent_gate.infrastructure.persistence.sqlite_action_execution import (
    SQLiteExecutionRepository,
)
from agentic_intent_gate.infrastructure.persistence.sqlite_v2_runs import SQLiteV2RunRepository
from agentic_intent_gate.infrastructure.tools.executable_registry import ExecutableRegistry
from agentic_intent_gate.infrastructure.tools.service_demo import (
    DemoNoteExecutor,
    provision_demo_store,
)
from agentic_intent_gate.ports.action_repository import ActionCommand, AdmissionCommand


class IsolatedActionFixture(SimpleNamespace):
    def __repr__(self):
        return "<isolated Action fixture; credentials omitted>"


@pytest.fixture
def private_capability_path(tmp_path):
    path = tmp_path / "private-capability"
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


@pytest.fixture
def auth_env(tmp_path, validator):
    token, verifier, policy = capability()
    principal = verifier.verify("Bearer " + token)
    runs = SQLiteV2RunRepository(tmp_path / "gate.sqlite3", validator)
    compiler = ActionCompiler(validator, (verifier.binding,))
    repo = SQLiteExecutionRepository(runs, compiler)
    run = advance(runs, RunState.Approved, Risk.SIDE_EFFECT)
    action = repo.admit(AdmissionCommand(run.run_id, 5, principal.subject_id, str(uuid4()))).action
    return IsolatedActionFixture(
        token=token,
        verifier=verifier,
        policy=policy,
        principal=principal,
        runs=runs,
        repo=repo,
        run=run,
        action=action,
        tmp_path=tmp_path,
        ledger=lambda: ledger(runs.path),
    )


def cmd(action, version=None, key=None):
    return ActionCommand(
        action.action_id, action.version if version is None else version, key or str(uuid4())
    )


@pytest.fixture
def demo_env(auth_env):
    e = auth_env
    e.demo_path = e.tmp_path / "downstream.sqlite3"
    provision_demo_store(e.demo_path, e.action.spec.execution_scope)
    e.executor = DemoNoteExecutor(e.demo_path, e.action.spec.execution_scope, gate_path=e.runs.path)
    e.registry = ExecutableRegistry(e.executor)
    e.calls = 0
    return e


@pytest.fixture
def execution_env(demo_env, monkeypatch):
    e = demo_env
    lock = Lock()
    original = e.executor.execute

    async def counted(spec, effect_key, context):
        with lock:
            e.calls += 1
        drained = getattr(e, "claims_drained", None)
        if drained is not None:
            # Race tests control this barrier: ALL competing short claim transactions
            # must finish before a diagnostic write can distinguish our own lock from theirs.
            event = asyncio.Event()
            with e.claim_wait_lock:
                e.claim_listener = (asyncio.get_running_loop(), event)
                if drained.is_set():
                    event.set()
            async with asyncio.timeout(2):
                await event.wait()
        # Independent writer proves the control-plane transaction is already released.
        with closing(sqlite3.connect(e.runs.path, timeout=0.01)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.rollback()
        return await original(spec, effect_key, context)

    monkeypatch.setattr(e.executor, "execute", counted)
    e.gateway = ActionExecutionGateway(e.repo, e.policy, e.registry)
    e.action = e.gateway.authorize(cmd(e.action), e.principal).action
    return e


def inject_fault(monkeypatch, stage, *, after_commit=False):
    class Fault(sqlite3.Connection):
        def execute(self, statement, parameters=()):
            if statement.startswith(stage):
                raise sqlite3.OperationalError("private injected storage detail")
            return super().execute(statement, parameters)

        def commit(self):
            if stage == "COMMIT":
                if after_commit:
                    super().commit()
                raise sqlite3.OperationalError("unconfirmed commit")
            return super().commit()

    def open_fault(path):
        connection = sqlite3.connect(path, isolation_level=None, factory=Fault)
        connection.row_factory = sqlite3.Row
        return connection

    monkeypatch.setattr(sqlite_actions, "open_existing", open_fault)
