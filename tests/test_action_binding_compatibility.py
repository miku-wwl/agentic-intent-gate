"""Retained identity remains readable after retirement, without granting eligibility."""

from dataclasses import replace

import pytest
from run_helpers import advance
from test_action_persistence import command, ledger

from agentic_intent_gate.application.action_compiler import ActionCompiler
from agentic_intent_gate.domain.action import ActionError
from agentic_intent_gate.domain.contracts import Risk
from agentic_intent_gate.domain.run import RunState
from agentic_intent_gate.infrastructure.persistence.sqlite_actions import SQLiteActionRepository
from agentic_intent_gate.infrastructure.persistence.sqlite_v2_runs import SQLiteV2RunRepository


@pytest.mark.parametrize("replacement", ["revised", "empty"])
def test_retired_binding_reads_unchanged_but_admission_denied(auth_env, replacement):
    runs, compiler = auth_env.runs, auth_env.repo.compiler
    original = SQLiteActionRepository(runs, compiler)
    run = advance(runs, RunState.Approved, Risk.SIDE_EFFECT)
    request = command(run)
    action = original.admit(request).action
    before = ledger(runs.path)
    bindings = (
        (replace(compiler.bindings[0], binding_revision="demo-binding-v2"),)
        if replacement == "revised"
        else ()
    )
    current = ActionCompiler(runs.validator, bindings)
    reopened = SQLiteActionRepository(SQLiteV2RunRepository(runs.path, runs.validator), current)
    assert reopened.get(action.action_id) == action
    assert reopened.list_for_run(run.run_id) == (action,)
    assert ledger(runs.path) == before
    with pytest.raises(ActionError):
        reopened.admit(request)
    assert ledger(runs.path) == before
