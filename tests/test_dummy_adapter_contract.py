"""Direct dummy contract tests; all retained effects live in test-owned SQLite files."""

import asyncio
import hashlib
import json
import sqlite3
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from threading import Barrier, Lock, get_ident
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agentic_intent_gate.adapters.dummy_executor import (
    ADAPTER_ID,
    BINDING_REVISION,
    TARGET,
    TOOL_NAME,
    TOOL_VERSION,
    DeterministicDummyExecutor,
    DummyReceiptStore,
    StoreApplyResult,
    StoreApplyStatus,
)
from agentic_intent_gate.adapters.executable_registry import ExecutableRegistry
from agentic_intent_gate.application.action_execution import classify
from agentic_intent_gate.domain.action import ActionError, ActionState, safe_result
from agentic_intent_gate.domain.run_validation import valid_uuid
from agentic_intent_gate.ports.tool_executor import (
    AttemptContext,
    ExecutionSpec,
    Outcome,
    ToolExecutor,
    ToolOutcome,
)

SIMULATED_SECRET = "SIMULATED_SECRET bearer=test-only-token SQL=/test-only/private.sqlite3"
NON_V4_UUID = "11111111-1111-1111-8111-abcdefabcdef"
UPPERCASE_UUID = "11111111-1111-4111-8111-ABCDEFABCDEF"
UNSET = object()


class SQLiteDummyStore:
    """Test-only store: one records row atomically owns both effect and unique receipt.

    The database is bound to one retained six-field identity. A different trusted
    scope receives IDENTITY_MISMATCH, including after reopening. Instrumentation
    lists/counters are never used to decide replay, conflict, or durability.
    """

    def __init__(self, path, scope):
        self.path = path
        self.scope = scope
        self.reject_record = None
        self.connection_factory = sqlite3.Connection
        self.barrier = None
        self.lock = Lock()
        self.calls = []
        self.results = []
        self.connections = []
        self.threads = set()
        self.active = 0
        self.peak_active = 0

    @classmethod
    def create(cls, path, scope):
        with closing(sqlite3.connect(path, isolation_level=None)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """CREATE TABLE store_identity (
                        singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                        tool_name TEXT NOT NULL, tool_version TEXT NOT NULL,
                        target TEXT NOT NULL, adapter_id TEXT NOT NULL,
                        binding_revision TEXT NOT NULL, execution_scope TEXT NOT NULL
                    ) STRICT"""
                )
                connection.execute(
                    """CREATE TABLE records (
                        effect_key TEXT PRIMARY KEY NOT NULL,
                        adapter_payload_digest TEXT NOT NULL,
                        execution_scope TEXT NOT NULL,
                        record TEXT NOT NULL,
                        receipt_reference TEXT UNIQUE NOT NULL
                    ) STRICT"""
                )
                connection.execute(
                    "INSERT INTO store_identity VALUES (1, ?, ?, ?, ?, ?, ?)",
                    (TOOL_NAME, TOOL_VERSION, TARGET, ADAPTER_ID, BINDING_REVISION, scope),
                )
                connection.commit()
            finally:
                if connection.in_transaction:
                    connection.rollback()
        return cls(path, scope)

    def note_call(self, *args):
        with self.lock:
            self.calls.append(args)
            self.threads.add(get_ident())

    @staticmethod
    def cleanup(connection):
        uncertain = False
        try:
            active = connection.in_transaction
        except Exception:
            # If inspection fails, still attempt rollback and physical close.
            active = True
            uncertain = True
        if active:
            try:
                connection.rollback()
            except Exception:
                uncertain = True
        try:
            connection.close()
        except Exception:
            uncertain = True
        if uncertain:
            raise sqlite3.OperationalError("test receipt store cleanup uncertain")

    def apply_once(self, effect_key, adapter_payload_digest, execution_scope, record):
        self.note_call(effect_key, adapter_payload_digest, execution_scope, record)
        connection = sqlite3.connect(
            self.path.as_uri() + "?mode=rw",
            uri=True,
            timeout=5,
            isolation_level=None,
            factory=self.connection_factory,
        )
        connection.row_factory = sqlite3.Row
        with self.lock:
            self.connections.append(connection)  # Strong references also prove cleanup.
            self.active += 1
            self.peak_active = max(self.peak_active, self.active)
        completed = False
        try:
            if self.barrier is not None:
                # All real connections overlap before competing for SQLite's write lock.
                self.barrier.wait(timeout=15)
            connection.execute("BEGIN IMMEDIATE")
            identity = connection.execute(
                "SELECT tool_name, tool_version, target, adapter_id, binding_revision, "
                "execution_scope FROM store_identity WHERE singleton = 1"
            ).fetchone()
            expected = (TOOL_NAME, TOOL_VERSION, TARGET, ADAPTER_ID, BINDING_REVISION, self.scope)
            if identity is None or tuple(identity) != expected or execution_scope != self.scope:
                result = StoreApplyResult(StoreApplyStatus.IDENTITY_MISMATCH)
            elif record == self.reject_record:
                result = StoreApplyResult(StoreApplyStatus.REJECTED)
            else:
                retained = connection.execute(
                    "SELECT * FROM records WHERE effect_key = ?", (effect_key,)
                ).fetchone()
                if retained is None:
                    receipt = str(uuid4())
                    connection.execute(
                        "INSERT INTO records VALUES (?, ?, ?, ?, ?)",
                        (effect_key, adapter_payload_digest, execution_scope, record, receipt),
                    )
                    result = StoreApplyResult(StoreApplyStatus.CREATED, receipt)
                elif retained["execution_scope"] != execution_scope:
                    result = StoreApplyResult(StoreApplyStatus.IDENTITY_MISMATCH)
                elif (
                    retained["adapter_payload_digest"] != adapter_payload_digest
                    or retained["record"] != record
                ):
                    result = StoreApplyResult(StoreApplyStatus.CONFLICT)
                elif not valid_uuid(retained["receipt_reference"]):
                    raise sqlite3.DatabaseError("invalid retained dummy receipt")
                else:
                    result = StoreApplyResult(
                        StoreApplyStatus.REPLAYED, retained["receipt_reference"]
                    )
            connection.commit()
            completed = True
        finally:
            try:
                self.cleanup(connection)
            except Exception:
                if completed:
                    raise  # A clean body with uncertain cleanup cannot confirm success.
                # Preserve an already-propagating primary exception, including cancellation.
            finally:
                with self.lock:
                    self.active -= 1
        with self.lock:
            self.results.append(result)
        return result


def snapshot(path):
    with closing(sqlite3.connect(path)) as connection:
        connection.row_factory = sqlite3.Row
        return [
            dict(row) for row in connection.execute("SELECT * FROM records ORDER BY effect_key")
        ]


def assert_closed(store):
    assert store.active == 0
    for connection in store.connections:
        with pytest.raises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")


def assert_second_writer(store):
    """Retain originals while an independent connection really INSERTs and COMMITs."""
    originals = tuple(store.connections)
    before = snapshot(store.path)
    with closing(sqlite3.connect(store.path, timeout=1, isolation_level=None)) as writer:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute(
            "INSERT INTO records VALUES (?, ?, ?, ?, ?)",
            (str(uuid4()), "writer-probe", store.scope, "independent writer", str(uuid4())),
        )
        writer.commit()
    assert len(snapshot(store.path)) == len(before) + 1
    assert tuple(store.connections) == originals


def spec_for(scope, record="alpha"):
    return ExecutionSpec(
        TOOL_NAME, TOOL_VERSION, TARGET, {"record": record}, ADAPTER_ID, BINDING_REVISION, scope
    )


def context_for(*, attempt_id=UNSET, deadline=None):
    return AttemptContext(
        str(uuid4()) if attempt_id is UNSET else attempt_id,
        asyncio.get_running_loop().time() + 30 if deadline is None else deadline,
        asyncio.Event(),
    )


async def invoke(env, *, spec=None, effect_key=UNSET, context=None) -> ToolOutcome:
    executor: ToolExecutor = env.executor
    return await executor.execute(
        env.spec if spec is None else spec,
        env.key if effect_key is UNSET else effect_key,
        context_for() if context is None else context,
    )


def assert_success(outcome, spec, key):
    assert outcome.category is Outcome.SUCCESS
    assert valid_uuid(outcome.receipt_reference)
    assert outcome.no_effect_reason is None
    assert (
        outcome.effect_key,
        outcome.execution_scope,
        outcome.target,
        outcome.payload_digest,
    ) == (key, spec.execution_scope, spec.target, spec.payload_digest())
    assert classify(outcome, spec, key) == safe_result(
        ActionState.Succeeded, outcome.receipt_reference
    )


def assert_failure(outcome, spec, key, reason):
    assert outcome.category is Outcome.CONFIRMED_FAILURE
    assert outcome.no_effect_reason == reason
    assert outcome.receipt_reference is None
    assert classify(outcome, spec, key) == safe_result(ActionState.Failed)


def assert_unknown(outcome, spec, key):
    assert outcome.category is Outcome.UNKNOWN_OUTCOME
    assert classify(outcome, spec, key) == safe_result(ActionState.Unknown)
    assert outcome.receipt_reference is None
    assert outcome.no_effect_reason is None


@pytest.fixture
def dummy_env(tmp_path):
    scope = str(uuid4())
    store: DummyReceiptStore = SQLiteDummyStore.create(tmp_path / "dummy.sqlite3", scope)
    return SimpleNamespace(
        store=store,
        scope=scope,
        key=str(uuid4()),
        spec=spec_for(scope),
        executor=DeterministicDummyExecutor(scope, store),
    )


async def test_valid_binding_and_exact_replay(dummy_env):
    e = dummy_env
    first = await invoke(e)
    retained = snapshot(e.store.path)
    second = await invoke(e)
    assert_success(first, e.spec, e.key)
    assert_success(second, e.spec, e.key)
    assert first == second
    assert [r.status for r in e.store.results] == [
        StoreApplyStatus.CREATED,
        StoreApplyStatus.REPLAYED,
    ]
    assert len(e.store.calls) == 2
    assert len(retained) == 1 and snapshot(e.store.path) == retained
    assert retained[0]["receipt_reference"] == first.receipt_reference
    assert retained[0]["record"] == "alpha"
    assert_closed(e.store)


async def test_payload_conflict_keeps_original_effect_and_receipt(dummy_env):
    e = dummy_env
    first = await invoke(e)
    retained = snapshot(e.store.path)
    altered = replace(e.spec, arguments={"record": "beta"})
    result = await invoke(e, spec=altered)
    assert_failure(result, altered, e.key, "PAYLOAD_CONFLICT")
    assert e.store.results[-1].status is StoreApplyStatus.CONFLICT
    assert snapshot(e.store.path) == retained
    assert retained[0]["receipt_reference"] == first.receipt_reference
    assert len(e.store.calls) == 2
    assert e.store.calls[0][1] != e.store.calls[1][1]


async def test_cross_scope_reopen_rejects_retained_identity(dummy_env):
    e = dummy_env
    await invoke(e)
    retained = snapshot(e.store.path)
    other_scope = str(uuid4())
    other_store = SQLiteDummyStore(e.store.path, other_scope)
    other = SimpleNamespace(
        store=other_store,
        key=e.key,
        spec=spec_for(other_scope),
        executor=DeterministicDummyExecutor(other_scope, other_store),
    )
    result = await invoke(other)
    assert_failure(result, other.spec, e.key, "STORE_IDENTITY_MISMATCH")
    assert other_store.results[-1].status is StoreApplyStatus.IDENTITY_MISMATCH
    assert len(other_store.calls) == 1 and snapshot(e.store.path) == retained
    assert other_store.calls[0][1] != e.store.calls[0][1]
    assert_closed(other_store)


async def test_retained_store_metadata_is_checked_each_call(dummy_env):
    e = dummy_env
    with closing(sqlite3.connect(e.store.path)) as connection:
        connection.execute("UPDATE store_identity SET binding_revision = ?", ("retired",))
        connection.commit()
    result = await invoke(e)
    assert_failure(result, e.spec, e.key, "STORE_IDENTITY_MISMATCH")
    assert len(e.store.calls) == 1 and snapshot(e.store.path) == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("tool_name", "wrong_tool"),
        ("tool_version", "2"),
        ("target", "wrong_target"),
        ("adapter_id", "wrong_adapter"),
        ("binding_revision", "dummy-v2"),
        ("execution_scope", "11111111-1111-4111-8111-abcdefabcdef"),
        ("execution_scope", "not-a-uuid"),
    ],
)
async def test_wrong_binding_never_dispatches(dummy_env, field, value):
    e = dummy_env
    spec = replace(e.spec, **{field: value})
    result = await invoke(e, spec=spec)
    assert_failure(result, spec, e.key, "INPUT_REJECTED")
    assert e.store.calls == [] and e.store.connections == []
    assert snapshot(e.store.path) == []


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"record": "alpha", "extra": "x"},
        {"other": "alpha"},
        {"record": ""},
        {"record": None},
        {"record": 1},
        {"record": True},
        {"record": []},
        {"record": {}},
        None,
        [],
        "alpha",
    ],
)
async def test_invalid_arguments_never_dispatch(dummy_env, arguments):
    e = dummy_env
    spec = replace(e.spec, arguments=arguments)
    result = await invoke(e, spec=spec)
    assert_failure(result, spec, e.key, "INPUT_REJECTED")
    assert e.store.calls == [] and snapshot(e.store.path) == []


@pytest.mark.parametrize("record", ["a" * 257, "界" * 86, "\0" * 169])
async def test_oversized_utf8_or_canonical_json_never_dispatches(dummy_env, record):
    e = dummy_env
    assert (
        len(record.encode()) > 256
        or len(json.dumps({"record": record}, ensure_ascii=False, separators=(",", ":")).encode())
        > 1024
    )
    spec = spec_for(e.scope, record)
    result = await invoke(e, spec=spec)
    assert_failure(result, spec, e.key, "INPUT_REJECTED")
    assert e.store.calls == [] and snapshot(e.store.path) == []


@pytest.mark.parametrize("record", ["a" * 256, "界" * 85, "\0" * 168])
async def test_bounded_utf8_and_json_edges_are_accepted(dummy_env, record):
    e = dummy_env
    spec = spec_for(e.scope, record)
    result = await invoke(e, spec=spec)
    assert_success(result, spec, e.key)
    assert snapshot(e.store.path)[0]["record"] == record
    assert len(e.store.calls) == 1


@pytest.mark.parametrize(
    "record",
    ["DROP TABLE records;", "$(rm -rf /)", "../../../etc/passwd", "<script>alert(1)</script>"],
)
async def test_malicious_looking_text_is_literal_sqlite_data(dummy_env, record):
    e = dummy_env
    spec = spec_for(e.scope, record)
    result = await invoke(e, spec=spec)
    assert_success(result, spec, e.key)
    assert len(snapshot(e.store.path)) == 1
    assert snapshot(e.store.path)[0]["record"] == record
    assert len(e.store.calls) == 1


@pytest.mark.parametrize("value", ["bad", "", NON_V4_UUID, UPPERCASE_UUID, None, 7])
@pytest.mark.parametrize("field", ["effect_key", "attempt_id"])
async def test_invalid_effect_or_attempt_identity_never_dispatches(dummy_env, field, value):
    e = dummy_env
    if field == "effect_key":
        result = await invoke(e, effect_key=value)
    else:
        result = await invoke(e, context=context_for(attempt_id=value))
    if field == "effect_key" and not isinstance(value, str):
        assert result.category is Outcome.UNKNOWN_OUTCOME
    else:
        assert result.category is Outcome.CONFIRMED_FAILURE
        assert result.no_effect_reason == "INPUT_REJECTED"
    assert e.store.calls == [] and snapshot(e.store.path) == []


@pytest.mark.parametrize("scope", ["bad", NON_V4_UUID, UPPERCASE_UUID, None])
def test_constructor_requires_trusted_uuid_scope(dummy_env, scope):
    with pytest.raises(ValueError):
        DeterministicDummyExecutor(scope, dummy_env.store)
    assert dummy_env.store.calls == []


def test_constructor_requires_store_and_dummy_is_not_production_registered(dummy_env):
    e = dummy_env
    with pytest.raises(TypeError):
        DeterministicDummyExecutor(e.scope, object())
    with pytest.raises(ActionError):
        ExecutableRegistry(e.executor)
    with pytest.raises(ActionError):
        ExecutableRegistry().resolve(e.spec)
    assert e.store.calls == []


async def test_expired_deadline_and_precancelled_context_never_dispatch(dummy_env):
    e = dummy_env
    expired = context_for(deadline=asyncio.get_running_loop().time() - 1)
    cancelled = context_for()
    cancelled.cancelled.set()
    for context in (expired, cancelled):
        assert_unknown(await invoke(e, context=context), e.spec, e.key)
    assert e.store.calls == [] and snapshot(e.store.path) == []


async def test_cancelled_error_propagates_without_retry(dummy_env, monkeypatch):
    e = dummy_env

    def cancelled(*args):
        e.store.note_call(*args)
        raise asyncio.CancelledError()

    monkeypatch.setattr(e.store, "apply_once", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await invoke(e)
    assert len(e.store.calls) == 1 and snapshot(e.store.path) == []


async def test_deadline_after_real_commit_is_unknown_without_retry(dummy_env, monkeypatch):
    e = dummy_env
    clock = [100.0]
    original = e.store.apply_once
    context = context_for(deadline=101.0)

    def committed(*args):
        result = original(*args)
        clock[0] = 102.0
        return result

    with monkeypatch.context() as patch:
        patch.setattr(asyncio.get_running_loop(), "time", lambda: clock[0])
        patch.setattr(e.store, "apply_once", committed)
        result = await invoke(e, context=context)
    assert_unknown(result, e.spec, e.key)
    assert len(e.store.calls) == 1 and len(snapshot(e.store.path)) == 1
    assert_closed(e.store)


async def test_cancellation_flag_after_real_commit_is_unknown(dummy_env, monkeypatch):
    e = dummy_env
    context = context_for()
    original = e.store.apply_once

    def committed(*args):
        result = original(*args)
        context.cancelled.set()
        return result

    monkeypatch.setattr(e.store, "apply_once", committed)
    assert_unknown(await invoke(e, context=context), e.spec, e.key)
    assert len(e.store.calls) == 1 and len(snapshot(e.store.path)) == 1


async def test_store_confirmed_pre_effect_rejection(dummy_env):
    e = dummy_env
    e.store.reject_record = "alpha"
    result = await invoke(e)
    assert_failure(result, e.spec, e.key, "INPUT_REJECTED")
    assert e.store.results == [StoreApplyResult(StoreApplyStatus.REJECTED)]
    assert len(e.store.calls) == 1 and snapshot(e.store.path) == []


@pytest.mark.parametrize(
    "response",
    [
        None,
        {"status": "CREATED", "receipt": "forged"},
        "SUCCESS",
        object(),
        StoreApplyResult("CREATED", "11111111-1111-4111-8111-abcdefabcdef"),
        StoreApplyResult("UNRECOGNIZED"),
        StoreApplyResult(StoreApplyStatus.CONFLICT, "11111111-1111-4111-8111-abcdefabcdef"),
    ],
)
async def test_malformed_store_result_is_unknown_one_dispatch(dummy_env, monkeypatch, response):
    e = dummy_env

    def malformed(*args):
        e.store.note_call(*args)
        return response

    monkeypatch.setattr(e.store, "apply_once", malformed)
    assert_unknown(await invoke(e), e.spec, e.key)
    assert len(e.store.calls) == 1 and snapshot(e.store.path) == []


@pytest.mark.parametrize("status", [StoreApplyStatus.CREATED, StoreApplyStatus.REPLAYED])
@pytest.mark.parametrize("receipt", [None, "", "not-a-uuid", NON_V4_UUID, UPPERCASE_UUID, 42])
async def test_malformed_receipt_is_unknown_one_dispatch(dummy_env, monkeypatch, status, receipt):
    e = dummy_env

    def malformed(*args):
        e.store.note_call(*args)
        return StoreApplyResult(status, receipt)

    monkeypatch.setattr(e.store, "apply_once", malformed)
    assert_unknown(await invoke(e), e.spec, e.key)
    assert len(e.store.calls) == 1 and snapshot(e.store.path) == []


async def test_malformed_retained_receipt_is_not_replaced(dummy_env):
    e = dummy_env
    await invoke(e)
    with closing(sqlite3.connect(e.store.path)) as connection:
        connection.execute("UPDATE records SET receipt_reference = ?", ("broken-receipt",))
        connection.commit()
    retained = snapshot(e.store.path)
    assert_unknown(await invoke(e), e.spec, e.key)
    assert snapshot(e.store.path) == retained and len(e.store.calls) == 2
    assert_closed(e.store)


@pytest.mark.parametrize(
    "changes",
    [
        {"effect_key": "11111111-1111-4111-8111-abcdefabcdef"},
        {"execution_scope": "11111111-1111-4111-8111-abcdefabcdef"},
        {"target": "wrong"},
        {"payload_digest": "0" * 64},
        {"receipt_reference": "not-a-uuid"},
        {"no_effect_reason": "INPUT_REJECTED"},
        {"category": "SUCCESS"},
    ],
)
async def test_forged_success_is_rejected_by_frozen_classifier(dummy_env, changes):
    e = dummy_env
    valid = await invoke(e)
    forged = replace(valid, **changes)
    assert classify(forged, e.spec, e.key) == safe_result(ActionState.Unknown)
    assert len(e.store.calls) == 1 and len(snapshot(e.store.path)) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"effect_key": "11111111-1111-4111-8111-abcdefabcdef"},
        {"execution_scope": "11111111-1111-4111-8111-abcdefabcdef"},
        {"target": "wrong"},
        {"payload_digest": "0" * 64},
        {"receipt_reference": "11111111-1111-4111-8111-abcdefabcdef"},
        {"no_effect_reason": None},
        {"no_effect_reason": "HTTP_500"},
        {"category": "CONFIRMED_FAILURE"},
    ],
)
async def test_forged_failure_is_rejected_by_frozen_classifier(dummy_env, changes):
    e = dummy_env
    e.store.reject_record = "alpha"
    valid = await invoke(e)
    assert_failure(valid, e.spec, e.key, "INPUT_REJECTED")
    assert classify(replace(valid, **changes), e.spec, e.key) == safe_result(ActionState.Unknown)
    assert len(e.store.calls) == 1 and snapshot(e.store.path) == []


async def test_secret_bearing_error_is_discarded_without_retry(
    dummy_env, monkeypatch, caplog, capsys
):
    e = dummy_env

    def uncertain(*args):
        e.store.note_call(*args)
        raise RuntimeError(SIMULATED_SECRET)

    monkeypatch.setattr(e.store, "apply_once", uncertain)
    result = await invoke(e)
    assert_unknown(result, e.spec, e.key)
    captured = capsys.readouterr()
    assert SIMULATED_SECRET not in repr(result) + caplog.text + captured.out + captured.err
    assert len(e.store.calls) == 1 and snapshot(e.store.path) == []


async def test_adapter_digest_binds_full_identity_and_is_separate_from_outcome_digest(dummy_env):
    e = dummy_env
    result = await invoke(e)
    expected = hashlib.sha256(
        json.dumps(
            {
                "identity_revision": "dummy-record-v1",
                "tool_name": "dummy_record",
                "tool_version": "1",
                "target": "test-local-dummy",
                "adapter_id": "day4-dummy",
                "binding_revision": "dummy-v1",
                "execution_scope": e.scope,
                "arguments": {"record": "alpha"},
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    assert e.store.calls == [(e.key, expected, e.scope, "alpha")]
    assert snapshot(e.store.path)[0]["adapter_payload_digest"] == expected
    assert result.payload_digest == e.spec.payload_digest() != expected
    assert classify(replace(result, payload_digest=expected), e.spec, e.key) == safe_result(
        ActionState.Unknown
    )
    with pytest.raises(TypeError):
        e.spec.arguments["record"] = "mutated"


@pytest.mark.parametrize("case", ["identical", "conflicting", "distinct_keys"])
def test_real_twenty_thread_sqlite_concurrency(dummy_env, case):
    e = dummy_env
    e.store.barrier = Barrier(20)
    work = [
        (
            spec_for(e.scope, "beta" if case == "conflicting" and index % 2 else "alpha"),
            str(uuid4()) if case == "distinct_keys" else e.key,
        )
        for index in range(20)
    ]

    def worker(item):
        spec, key = item
        return spec, key, asyncio.run(invoke(e, spec=spec, effect_key=key))

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(worker, work))
    counts = Counter(result.status for result in e.store.results)
    rows = snapshot(e.store.path)
    assert len(e.store.calls) == len(e.store.connections) == len(e.store.threads) == 20
    assert e.store.peak_active == 20
    assert len({id(connection) for connection in e.store.connections}) == 20
    assert len({row["receipt_reference"] for row in rows}) == len(rows)
    if case == "identical":
        assert counts == {StoreApplyStatus.CREATED: 1, StoreApplyStatus.REPLAYED: 19}
        assert len(rows) == 1
        assert {outcome.receipt_reference for _, _, outcome in results} == {
            rows[0]["receipt_reference"]
        }
    elif case == "conflicting":
        assert counts == {
            StoreApplyStatus.CREATED: 1,
            StoreApplyStatus.REPLAYED: 9,
            StoreApplyStatus.CONFLICT: 10,
        }
        assert len(rows) == 1 and rows[0]["record"] in {"alpha", "beta"}
    else:
        assert counts == {StoreApplyStatus.CREATED: 20}
        assert len(rows) == 20 and {row["effect_key"] for row in rows} == {key for _, key in work}
    for spec, key, outcome in results:
        if case == "conflicting" and spec.arguments["record"] != rows[0]["record"]:
            assert_failure(outcome, spec, key, "PAYLOAD_CONFLICT")
        else:
            assert_success(outcome, spec, key)
            assert any(row["receipt_reference"] == outcome.receipt_reference for row in rows)
    assert_closed(e.store)
    assert_second_writer(e.store)


async def test_restart_new_store_retains_receipt_and_conflict(dummy_env):
    e = dummy_env
    first = await invoke(e)
    retained = snapshot(e.store.path)
    path = e.store.path
    assert_closed(e.store)
    del e.executor
    del e.store
    e.store = SQLiteDummyStore(path, e.scope)
    e.executor = DeterministicDummyExecutor(e.scope, e.store)
    replay = await invoke(e)
    assert_success(replay, e.spec, e.key)
    assert replay.receipt_reference == first.receipt_reference
    altered = spec_for(e.scope, "beta")
    conflict = await invoke(e, spec=altered)
    assert_failure(conflict, altered, e.key, "PAYLOAD_CONFLICT")
    assert [result.status for result in e.store.results] == [
        StoreApplyStatus.REPLAYED,
        StoreApplyStatus.CONFLICT,
    ]
    assert snapshot(path) == retained and len(e.store.calls) == 2
    assert_closed(e.store)


def install_connection_fault(store, phase, cleanup_faults=()):
    """Controlled driver faults operate around actual SQLite writes/COMMIT/release."""

    class FaultConnection(sqlite3.Connection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.rollback_calls = 0
            self.close_calls = 0
            self.committed = False

        def execute(self, sql, parameters=()):
            if phase == "insert" and sql.startswith("INSERT INTO records"):
                raise sqlite3.OperationalError(SIMULATED_SECRET)
            return super().execute(sql, parameters)

        def commit(self):
            if phase == "before_commit":
                raise sqlite3.OperationalError(SIMULATED_SECRET)
            super().commit()
            self.committed = True
            if phase == "after_commit":
                raise sqlite3.OperationalError(SIMULATED_SECRET)

        @property
        def in_transaction(self):
            if "inspect" in cleanup_faults:
                raise sqlite3.OperationalError(SIMULATED_SECRET)
            return super().in_transaction

        def rollback(self):
            self.rollback_calls += 1
            if "rollback" in cleanup_faults:
                raise sqlite3.OperationalError(SIMULATED_SECRET)
            return super().rollback()

        def close(self):
            self.close_calls += 1
            super().close()  # Release physically before an injected acknowledgment failure.
            if "close" in cleanup_faults:
                raise sqlite3.OperationalError(SIMULATED_SECRET)

    store.connection_factory = FaultConnection


@pytest.mark.parametrize("phase", ["insert", "before_commit"])
async def test_transaction_failure_rolls_back_no_partial_effect(dummy_env, phase):
    e = dummy_env
    install_connection_fault(e.store, phase)
    result = await invoke(e)
    assert_unknown(result, e.spec, e.key)
    assert len(e.store.calls) == 1 and snapshot(e.store.path) == []
    original = e.store.connections[0]
    assert original.rollback_calls == original.close_calls == 1 and not original.committed
    assert SIMULATED_SECRET not in repr(result)
    assert_closed(e.store)
    assert_second_writer(e.store)


async def test_commit_ack_loss_is_unknown_then_only_explicit_replay(dummy_env):
    e = dummy_env
    install_connection_fault(e.store, "after_commit")
    result = await invoke(e)
    assert_unknown(result, e.spec, e.key)
    rows = snapshot(e.store.path)
    assert len(e.store.calls) == len(rows) == 1
    assert valid_uuid(rows[0]["receipt_reference"])
    original = e.store.connections[0]
    assert original.committed and original.rollback_calls == 0 and original.close_calls == 1
    assert_closed(e.store)
    e.store.connection_factory = sqlite3.Connection
    replay = await invoke(e)  # A new explicit test request, never an automatic retry.
    assert_success(replay, e.spec, e.key)
    assert replay.receipt_reference == rows[0]["receipt_reference"]
    assert len(e.store.calls) == 2 and snapshot(e.store.path) == rows
    assert e.store.results == [
        StoreApplyResult(StoreApplyStatus.REPLAYED, replay.receipt_reference)
    ]


@pytest.mark.parametrize(
    "phase,faults,durable",
    [
        ("before_commit", ("inspect",), 0),
        ("before_commit", ("rollback",), 0),
        ("normal", ("close",), 1),
        ("before_commit", ("inspect", "rollback", "close"), 0),
    ],
)
async def test_cleanup_faults_still_physically_close_and_allow_second_writer(
    dummy_env, phase, faults, durable
):
    e = dummy_env
    install_connection_fault(e.store, phase, faults)
    result = await invoke(e)
    assert_unknown(result, e.spec, e.key)
    assert len(e.store.calls) == 1 and len(snapshot(e.store.path)) == durable
    original = e.store.connections[0]  # Keep this strong reference throughout writer proof.
    assert original.close_calls == 1
    assert original.rollback_calls == (1 if phase == "before_commit" else 0)
    assert SIMULATED_SECRET not in repr(result)
    assert_closed(e.store)
    assert_second_writer(e.store)
    assert e.store.connections[0] is original


async def test_healthy_cleanup_and_sqlite_unique_effect_and_receipt_constraints(dummy_env):
    e = dummy_env
    await invoke(e)
    row = snapshot(e.store.path)[0]
    assert_closed(e.store)
    with closing(sqlite3.connect(e.store.path, isolation_level=None)) as connection:
        for key, receipt in [(e.key, str(uuid4())), (str(uuid4()), row["receipt_reference"])]:
            connection.execute("BEGIN IMMEDIATE")
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO records VALUES (?, ?, ?, ?, ?)",
                    (key, "duplicate-probe", e.scope, "probe", receipt),
                )
            connection.rollback()
    assert snapshot(e.store.path) == [row]
    assert_second_writer(e.store)


def test_store_rejects_changed_digest_even_when_literal_record_matches(dummy_env):
    e = dummy_env
    first = e.store.apply_once(e.key, "a" * 64, e.scope, "alpha")
    retained = snapshot(e.store.path)
    conflict = e.store.apply_once(e.key, "b" * 64, e.scope, "alpha")
    assert first.status is StoreApplyStatus.CREATED
    assert conflict == StoreApplyResult(StoreApplyStatus.CONFLICT)
    assert retained[0]["receipt_reference"] == first.receipt_reference
    assert snapshot(e.store.path) == retained and len(e.store.calls) == 2
    assert_closed(e.store)
