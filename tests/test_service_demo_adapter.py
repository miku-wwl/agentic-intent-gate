import asyncio
import sqlite3
from contextlib import closing
from dataclasses import replace
from uuid import uuid4

import pytest

import agentic_intent_gate.infrastructure.tools.service_demo as service_demo
from agentic_intent_gate.domain.action import ActionError
from agentic_intent_gate.infrastructure.tools.executable_registry import ExecutableRegistry
from agentic_intent_gate.infrastructure.tools.service_demo import DemoNoteExecutor
from agentic_intent_gate.ports.tool_executor import AttemptContext, ExecutionSpec, Outcome


def downstream(path):
    with closing(sqlite3.connect(path)) as connection:
        return {
            t: connection.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall()
            for t in ("resource", "notes", "receipts")
        }


async def invoke(e, spec=None, *, attempt_id=None):
    return await e.executor.execute(
        spec or ExecutionSpec.from_compiled(e.action.spec),
        e.action.effect_key,
        AttemptContext(
            attempt_id or str(uuid4()), asyncio.get_running_loop().time() + 2, asyncio.Event()
        ),
    )


async def test_real_external_effect_and_keyed_payload_dedup(demo_env):
    e = demo_env
    before_gate = e.ledger()
    first, second = await invoke(e), await invoke(e)
    assert first == second and first.category is Outcome.SUCCESS
    data = downstream(e.demo_path)
    assert len(data["notes"]) == len(data["receipts"]) == 1
    assert data["notes"][0][2] == e.action.spec.normalized_arguments["note"]
    assert data["receipts"][0][0] == e.action.effect_key
    assert first.receipt_reference == data["notes"][0][0]
    altered = replace(
        ExecutionSpec.from_compiled(e.action.spec),
        arguments={"service_id": "service-demo", "note": "different"},
    )
    conflict = await invoke(e, altered)
    assert (
        conflict.category is Outcome.CONFIRMED_FAILURE
        and conflict.no_effect_reason == "PAYLOAD_CONFLICT"
    )
    assert downstream(e.demo_path) == data and e.ledger() == before_gate


@pytest.mark.parametrize(
    "field,value",
    [
        ("target", "other"),
        ("tool_name", "get_service_record"),
        ("tool_version", "2"),
        ("adapter_id", "evil"),
        ("execution_scope", str(uuid4())),
        ("arguments", {"service_id": "service-demo", "note": "x", "url": "https://evil.invalid"}),
    ],
)
async def test_untrusted_input_and_registry_substitution_denied(demo_env, field, value):
    e = demo_env
    spec = replace(ExecutionSpec.from_compiled(e.action.spec), **{field: value})
    result = await invoke(e, spec)
    assert result.category is Outcome.CONFIRMED_FAILURE
    assert not downstream(e.demo_path)["notes"]
    if field != "arguments":
        with pytest.raises(ActionError):
            e.registry.resolve(spec)


async def test_missing_store_never_created_or_reset(auth_env):
    e = auth_env
    e.demo_path = e.tmp_path / "missing.sqlite3"
    e.executor = DemoNoteExecutor(e.demo_path, e.action.spec.execution_scope, gate_path=e.runs.path)
    assert (await invoke(e)).category is Outcome.UNKNOWN_OUTCOME
    assert not e.demo_path.exists()


async def test_changed_backing_store_identity_has_no_effect(demo_env):
    e = demo_env
    with closing(sqlite3.connect(e.demo_path)) as connection:
        connection.execute("UPDATE resource SET execution_scope=?", (str(uuid4()),))
        connection.commit()
    result = await invoke(e)
    assert (
        result.category is Outcome.CONFIRMED_FAILURE
        and result.no_effect_reason == "STORE_IDENTITY_MISMATCH"
    )
    assert not downstream(e.demo_path)["notes"]


def test_metadata_and_arbitrary_callables_are_not_executable(demo_env):
    with pytest.raises(ActionError):
        ExecutableRegistry().resolve(demo_env.action.spec)
    with pytest.raises(ActionError):
        ExecutableRegistry(lambda *args: None)
    with pytest.raises(ActionError):
        DemoNoteExecutor(
            demo_env.runs.path, demo_env.action.spec.execution_scope, gate_path=demo_env.runs.path
        )


async def test_cancelled_context_has_no_append(demo_env):
    e = demo_env
    cancel = asyncio.Event()
    cancel.set()
    result = await e.executor.execute(
        ExecutionSpec.from_compiled(e.action.spec),
        e.action.effect_key,
        AttemptContext(str(uuid4()), asyncio.get_running_loop().time() + 1, cancel),
    )
    assert result.category is Outcome.UNKNOWN_OUTCOME and not downstream(e.demo_path)["notes"]


@pytest.mark.parametrize("stage", ["INSERT INTO notes", "INSERT INTO receipts", "COMMIT"])
async def test_downstream_note_receipt_fault_is_atomic_unknown(demo_env, monkeypatch, stage):
    e = demo_env

    class Fault(sqlite3.Connection):
        def execute(self, sql, params=()):
            if sql.startswith(stage):
                raise sqlite3.OperationalError("downstream fault")
            return super().execute(sql, params)

        def commit(self):
            if stage == "COMMIT":
                raise sqlite3.OperationalError("unconfirmed commit")
            return super().commit()

    def open_fault(path):
        connection = sqlite3.connect(path, isolation_level=None, factory=Fault)
        connection.row_factory = sqlite3.Row
        return connection

    monkeypatch.setattr(service_demo, "open_existing", open_fault)
    assert (await invoke(e)).category is Outcome.UNKNOWN_OUTCOME
    assert not downstream(e.demo_path)["notes"] and not downstream(e.demo_path)["receipts"]
