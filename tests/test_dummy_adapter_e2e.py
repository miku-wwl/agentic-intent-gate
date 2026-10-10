"""DIRECT ADAPTER BOUNDARY E2E, not production Action/HTTP/browser dispatch."""

import builtins
import hashlib
import json
import os
import sqlite3
import subprocess
from contextlib import closing
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from test_dummy_adapter_contract import (
    SQLiteDummyStore,
    assert_closed,
    assert_failure,
    assert_second_writer,
    assert_success,
    assert_unknown,
    context_for,
    install_connection_fault,
    spec_for,
)

from agentic_intent_gate.application.action_execution import classify
from agentic_intent_gate.domain.action import ActionState, safe_result
from agentic_intent_gate.domain.run_validation import valid_uuid
from agentic_intent_gate.infrastructure.tools.dummy_executor import (
    DeterministicDummyExecutor,
    StoreApplyStatus,
)
from agentic_intent_gate.ports.tool_executor import ToolExecutor


@pytest.fixture
def boundary(tmp_path):
    scope, key = str(uuid4()), str(uuid4())
    store = SQLiteDummyStore.create(tmp_path / "dummy.sqlite3", scope)
    return SimpleNamespace(
        scope=scope,
        key=key,
        store=store,
        spec=spec_for(scope),
        executor=DeterministicDummyExecutor(scope, store),
    )


def durable_state(path):
    """Fresh, explicitly closed SQL reader: no instrumentation is durability evidence."""
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)) as reader:
        reader.row_factory = sqlite3.Row
        rows = [dict(row) for row in reader.execute("SELECT * FROM records ORDER BY effect_key")]
        counts = tuple(
            reader.execute(
                "SELECT COUNT(*), COUNT(DISTINCT receipt_reference) FROM records"
            ).fetchone()
        )
    return rows, counts


def assert_retained(path, spec, key, receipt):
    # Independently compute full adapter identity, not the demo-labelled outcome digest.
    identity = {
        "identity_revision": "dummy-record-v1",
        "tool_name": spec.tool_name,
        "tool_version": spec.tool_version,
        "target": spec.target,
        "adapter_id": spec.adapter_id,
        "binding_revision": spec.binding_revision,
        "execution_scope": spec.execution_scope,
        "arguments": {"record": spec.arguments["record"]},
    }
    adapter_digest = hashlib.sha256(
        json.dumps(
            identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    ).hexdigest()
    assert valid_uuid(key) and valid_uuid(spec.execution_scope) and valid_uuid(receipt)
    assert durable_state(path) == (
        [
            {
                "effect_key": key,
                "adapter_payload_digest": adapter_digest,
                "execution_scope": spec.execution_scope,
                "record": spec.arguments["record"],
                "receipt_reference": receipt,
            }
        ],
        (1, 1),
    )


async def test_direct_boundary_success_reopen_replay_and_conflict(boundary):
    e = boundary
    context = context_for()
    assert valid_uuid(context.attempt_id)
    executor: ToolExecutor = e.executor
    first = await executor.execute(e.spec, e.key, context)
    assert_success(first, e.spec, e.key)
    assert_retained(e.store.path, e.spec, e.key, first.receipt_reference)
    assert [result.status for result in e.store.results] == [StoreApplyStatus.CREATED]
    assert len(e.store.calls) == 1
    assert_closed(e.store)  # Keep strong connection references; do not depend on GC.

    reopened = SQLiteDummyStore(e.store.path, e.scope)
    restarted: ToolExecutor = DeterministicDummyExecutor(e.scope, reopened)
    replay = await restarted.execute(e.spec, e.key, context_for())
    assert_success(replay, e.spec, e.key)
    assert replay == first
    assert_retained(reopened.path, e.spec, e.key, first.receipt_reference)

    changed = spec_for(e.scope, "different record")
    conflict = await restarted.execute(changed, e.key, context_for())
    assert_failure(conflict, changed, e.key, "PAYLOAD_CONFLICT")
    assert [result.status for result in reopened.results] == [
        StoreApplyStatus.REPLAYED,
        StoreApplyStatus.CONFLICT,
    ]
    assert len(reopened.calls) == 2
    assert reopened.calls[0][1] != reopened.calls[1][1]
    assert_retained(reopened.path, e.spec, e.key, first.receipt_reference)
    assert_closed(e.store)
    assert_closed(reopened)


async def test_cross_scope_reopen_cannot_replay_or_change_retained_effect(boundary):
    e = boundary
    first = await e.executor.execute(e.spec, e.key, context_for())
    assert_success(first, e.spec, e.key)
    assert_closed(e.store)
    other_scope = str(uuid4())
    other_spec = spec_for(other_scope)

    # Wrong call scope is rejected before the original store receives anything.
    denied = await e.executor.execute(other_spec, e.key, context_for())
    assert_failure(denied, other_spec, e.key, "INPUT_REJECTED")
    assert len(e.store.calls) == 1

    # Even a newly configured executor/store cannot override the retained DB identity.
    foreign_store = SQLiteDummyStore(e.store.path, other_scope)
    foreign_executor = DeterministicDummyExecutor(other_scope, foreign_store)
    denied_replay = await foreign_executor.execute(other_spec, e.key, context_for())
    assert_failure(denied_replay, other_spec, e.key, "STORE_IDENTITY_MISMATCH")
    assert len(foreign_store.calls) == 1
    assert foreign_store.results[0].status is StoreApplyStatus.IDENTITY_MISMATCH
    assert_retained(e.store.path, e.spec, e.key, first.receipt_reference)
    assert_closed(e.store)
    assert_closed(foreign_store)


@pytest.mark.parametrize("case", ["binding", "arguments"])
async def test_invalid_boundary_input_has_no_sqlite_dispatch(boundary, case):
    e = boundary
    invalid = (
        replace(e.spec, adapter_id="model-selected")
        if case == "binding"
        else replace(e.spec, arguments={"record": "alpha", "url": "https://example.invalid"})
    )
    result = await e.executor.execute(invalid, e.key, context_for())
    assert_failure(result, invalid, e.key, "INPUT_REJECTED")
    assert e.store.calls == e.store.connections == e.store.results == []
    assert durable_state(e.store.path) == ([], (0, 0))
    assert_closed(e.store)


async def test_real_commit_ack_loss_unknown_then_explicit_reopen_replay(boundary):
    e = boundary
    install_connection_fault(e.store, "after_commit")
    uncertain = await e.executor.execute(e.spec, e.key, context_for())
    assert_unknown(uncertain, e.spec, e.key)
    assert len(e.store.calls) == len(e.store.connections) == 1
    assert e.store.calls[0][0] == e.key
    assert e.store.results == []
    original_connection = e.store.connections[0]
    assert original_connection.committed
    assert original_connection.rollback_calls == 0 and original_connection.close_calls == 1
    assert_closed(e.store)
    rows, counts = durable_state(e.store.path)
    assert counts == (1, 1)
    receipt = rows[0]["receipt_reference"]
    assert_retained(e.store.path, e.spec, e.key, receipt)
    assert len(e.store.calls) == 1  # Unknown did not trigger retry or generate another key.

    # A separate explicit test operation, not an automatic recovery/redispatch path.
    reopened = SQLiteDummyStore(e.store.path, e.scope)
    restarted = DeterministicDummyExecutor(e.scope, reopened)
    replay = await restarted.execute(e.spec, e.key, context_for())
    assert_success(replay, e.spec, e.key)
    assert replay.receipt_reference == receipt
    assert len(e.store.calls) == len(reopened.calls) == 1
    assert reopened.results[0].status is StoreApplyStatus.REPLAYED
    assert_retained(reopened.path, e.spec, e.key, receipt)
    assert_closed(reopened)
    assert e.store.connections[0] is original_connection


async def test_model_like_record_is_literal_not_executable_authority(boundary, monkeypatch):
    e = boundary
    record = (
        "import day4_untrusted_module; shell=echo forbidden; "
        "url=https://example.invalid; path=../not-authority.txt; SQL=DROP TABLE records; --"
    )
    spec = spec_for(e.scope, record)
    original_import = builtins.__import__

    def reject_authority(*args, **kwargs):
        pytest.fail("record text attempted executable, network or file authority")

    def guarded_import(name, *args, **kwargs):
        if name == "day4_untrusted_module":
            reject_authority()
        return original_import(name, *args, **kwargs)

    # Only guard this real adapter invocation; SQLite itself remains real and unmocked.
    with monkeypatch.context() as guard:
        guard.setattr(builtins, "__import__", guarded_import)
        guard.setattr(builtins, "open", reject_authority)
        guard.setattr(os, "system", reject_authority)
        guard.setattr(subprocess, "Popen", reject_authority)
        guard.setattr(httpx.Client, "send", reject_authority)
        guard.setattr(httpx.AsyncClient, "send", reject_authority)
        outcome = await e.executor.execute(spec, e.key, context_for())
    assert_success(outcome, spec, e.key)
    assert_retained(e.store.path, spec, e.key, outcome.receipt_reference)
    assert e.store.calls[0][3] == record and len(e.store.calls) == 1
    assert sorted(path.name for path in e.store.path.parent.iterdir()) == ["dummy.sqlite3"]
    assert_closed(e.store)


async def test_real_success_cannot_hide_malformed_or_forged_correlation(boundary):
    e = boundary
    valid = await e.executor.execute(e.spec, e.key, context_for())
    assert_success(valid, e.spec, e.key)
    malformed = replace(valid, receipt_reference="not-a-uuid")
    forged = replace(valid, effect_key=str(uuid4()))
    for outcome in (malformed, forged):
        assert classify(outcome, e.spec, e.key) == safe_result(ActionState.Unknown)
    assert_retained(e.store.path, e.spec, e.key, valid.receipt_reference)
    assert len(e.store.calls) == 1
    assert_closed(e.store)


async def test_closed_connections_allow_real_independent_writer_commit(boundary):
    e = boundary
    first = await e.executor.execute(e.spec, e.key, context_for())
    assert_success(first, e.spec, e.key)
    assert_retained(e.store.path, e.spec, e.key, first.receipt_reference)
    originals = tuple(e.store.connections)
    assert originals
    assert_closed(e.store)
    assert_second_writer(e.store)  # Real BEGIN IMMEDIATE, INSERT, COMMIT; one-second timeout.
    rows, counts = durable_state(e.store.path)
    assert counts == (2, 2)  # Second row is an explicit writer probe, not a duplicate effect.
    retained = [row for row in rows if row["effect_key"] == e.key]
    assert len(retained) == 1 and retained[0]["receipt_reference"] == first.receipt_reference
    assert [row["record"] for row in rows if row["effect_key"] != e.key] == ["independent writer"]
    assert tuple(e.store.connections) == originals
    assert_closed(e.store)
