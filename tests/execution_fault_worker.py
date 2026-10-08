"""Owned test child only: real HTTP/SQLite/OS-lock barriers, never a product entrypoint.

Private authority arrives through stdin, not argv, environment, files or telemetry.
The parent kills only this Popen handle after checking nonce, PID and exact phase.
"""

import asyncio
import json
import os
import socket
import sqlite3
import sys
import threading
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import uvicorn

from agentic_intent_gate.adapters import service_demo, sqlite_actions
from agentic_intent_gate.adapters.executable_registry import ExecutableRegistry
from agentic_intent_gate.adapters.local_capability import CapabilityVerifier, LocalDemoAuthorization
from agentic_intent_gate.adapters.local_http import create_app
from agentic_intent_gate.adapters.service_demo import DemoNoteExecutor
from agentic_intent_gate.adapters.single_owner import single_owner
from agentic_intent_gate.adapters.sqlite_action_execution import SQLiteExecutionRepository
from agentic_intent_gate.adapters.sqlite_v2_runs import SQLiteV2RunRepository
from agentic_intent_gate.adapters.tool_catalog import build_validator
from agentic_intent_gate.application.action_compiler import ActionCompiler
from agentic_intent_gate.application.action_execution import ActionExecutionGateway
from agentic_intent_gate.application.generate_plan import PlanningService
from agentic_intent_gate.application.recover_actions import recover_actions
from agentic_intent_gate.application.recover_runs import recover_runs
from agentic_intent_gate.application.runs import RunService
from agentic_intent_gate.domain.action import ActionError
from agentic_intent_gate.ports.tool_executor import AttemptContext, ExecutionSpec


class ForbiddenProvider:
    async def generate_plan(self, *args, **kwargs):
        raise AssertionError("Recovery must not call a model")


def main():
    config = json.loads(sys.stdin.readline())
    nonce, window = config["nonce"], config.get("window", "")
    released = threading.Event()
    loop = None
    tasks = []
    server = None
    async_release = None

    def emit(phase, **values):
        event = {"nonce": nonce, "pid": os.getpid(), "phase": phase, **values}
        print(json.dumps(event), flush=True)

    def record_call():
        with Path(config["journal"]).open("a", encoding="ascii") as stream:
            stream.write(json.dumps({"nonce": nonce, "pid": os.getpid(), "call": 1}) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def barrier(phase):
        emit(phase)
        if not released.wait(15):
            raise RuntimeError("Owned test barrier expired")

    def control():
        for line in sys.stdin:
            operation = json.loads(line)["operation"]
            if operation == "release":
                released.set()
                if loop is not None:
                    loop.call_soon_threadsafe(async_release.set)
                if config.get("adapter_port_test"):
                    return
            elif operation == "cancel" and tasks:
                loop.call_soon_threadsafe(tasks[-1].cancel)
            elif operation == "stop" and server is not None:
                server.should_exit = True
                return

    class GateConnection:
        def __init__(self, inner):
            self.inner, self.kind = inner, None

        def __getattr__(self, name):
            return getattr(self.inner, name)

        @property
        def row_factory(self):
            return self.inner.row_factory

        @row_factory.setter
        def row_factory(self, value):
            self.inner.row_factory = value

        def execute(self, statement, parameters=()):
            if statement.startswith("INSERT INTO actions"):
                self.kind = "admit"
            elif statement.startswith("INSERT INTO action_authorizations"):
                self.kind = "authorize"
            elif statement.startswith("INSERT INTO action_attempts"):
                self.kind = "claim"
            elif statement.startswith("UPDATE actions SET") and parameters[0] in {
                "Succeeded",
                "Failed",
                "Unknown",
            }:
                self.kind = "finish"
            if self.kind == "finish" and window == "result-write-fault":
                raise sqlite3.OperationalError("Owned result write fault")
            return self.inner.execute(statement, parameters)

        def commit(self):
            if (self.kind, window) in {
                ("claim", "A"),
                ("admit", "H-admit"),
                ("authorize", "H-authorize"),
            }:
                barrier(window)
            if self.kind == "finish" and window in {"result-commit-fault", "result-close"}:
                if window == "result-close":
                    self.inner.close()
                raise sqlite3.OperationalError("Owned uncertain result persistence")
            self.inner.commit()
            if (self.kind, window) in {
                ("claim", "B"),
                ("finish", "F"),
                ("finish", "G"),
                ("admit", "admit-response-loss"),
                ("authorize", "authorize-response-loss"),
            }:
                barrier(window)
            if self.kind == "finish" and window == "result-after-commit":
                raise sqlite3.OperationalError("Owned lost commit acknowledgement")

    class DemoConnection:
        def __init__(self, inner):
            self.inner = inner

        def __getattr__(self, name):
            return getattr(self.inner, name)

        @property
        def row_factory(self):
            return self.inner.row_factory

        @row_factory.setter
        def row_factory(self, value):
            self.inner.row_factory = value

        def commit(self):
            self.inner.commit()
            if window == "D":
                barrier("D")
            if window in {"G", "downstream-response-loss"}:
                raise sqlite3.OperationalError("Owned lost downstream acknowledgement")

    async def serve():
        nonlocal loop, async_release, server
        loop = asyncio.get_running_loop()
        async_release = asyncio.Event()
        validator = build_validator()
        runs = SQLiteV2RunRepository(config["gate"], validator)
        executor = DemoNoteExecutor(config["demo"], config["scope"], gate_path=config["gate"])
        if config.get("retired"):
            executor.binding = replace(executor.binding, binding_revision="demo-binding-v2")
        verifier = CapabilityVerifier(
            config["token"],
            subject_id="local-demo",
            binding=executor.binding,
            operations=("read", "admit", "authorize", "execute"),
        )
        policy = LocalDemoAuthorization(verifier, policy_revision="demo-policy-v1")
        registry = ExecutableRegistry(executor)
        repo = SQLiteExecutionRepository(runs, ActionCompiler(validator, registry.bindings))
        recover_runs(runs)
        recovered = recover_actions(repo, predecessor_stopped=True)
        for _ in range(config.get("recover_repetitions", 1) - 1):
            assert recover_actions(repo, predecessor_stopped=True) == 0
        original_gate, original_demo = sqlite_actions.open_existing, service_demo.open_existing
        sqlite_actions.open_existing = lambda path: GateConnection(original_gate(path))
        service_demo.open_existing = lambda path: DemoConnection(original_demo(path))
        original_execute, original_claim = executor.execute, repo.claim
        drained, completed = asyncio.Event(), 0

        def claim(*args):
            nonlocal completed
            try:
                return original_claim(*args)
            finally:
                completed += 1
                if completed == config.get("contenders"):
                    drained.set()

        repo.claim = claim

        async def execute(spec, effect_key, context):
            tasks.append(asyncio.current_task())
            if config.get("contenders"):
                await drained.wait()
            # Durable TEST telemetry only; never modifies either business schema.
            record_call()
            with closing(sqlite3.connect(config["gate"], timeout=0.02)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.rollback()
            if window == "C":
                barrier("C")
            if window == "active":
                emit("active")
                await async_release.wait()
            result = await original_execute(spec, effect_key, context)
            if window == "E":
                assert result.no_effect_reason == "STORE_IDENTITY_MISMATCH"
                barrier("E")
            if window == "adapter-exception":
                raise RuntimeError("Owned adapter exception after effect")
            if window == "result-busy":
                barrier("result-busy")
            if window in {"timeout", "cancel"}:
                emit(window)
                await async_release.wait()
            return result

        executor.execute = execute
        planning = PlanningService(ForbiddenProvider(), validator)
        gateway = ActionExecutionGateway(
            repo, policy, registry, tool_timeout_seconds=0.2 if window == "timeout" else 5
        )
        app = create_app(
            planning,
            run_service=RunService(runs, planning),
            action_gateway=gateway,
            capability_verifier=verifier,
        )
        with closing(socket.socket()) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(128)
            server = uvicorn.Server(
                uvicorn.Config(
                    app,
                    log_level="critical",
                    access_log=False,
                    lifespan="off",
                    timeout_graceful_shutdown=1,
                )
            )
            serving = asyncio.create_task(server.serve(sockets=[listener]))
            # Uvicorn exposes a started boolean, not a readiness Event.
            while not server.started and not serving.done():  # noqa: ASYNC110
                await asyncio.sleep(0.005)
            emit("ready", port=listener.getsockname()[1], recovered=recovered)
            await serving

    async def adapter_port_test():
        # Reviewed downstream port only: no gate claim/authorize/execute/recovery.
        # Competing provider connections are not competing gate owners.
        validator = build_validator()
        runs = SQLiteV2RunRepository(config["gate"], validator)
        executor = DemoNoteExecutor(config["demo"], config["scope"], gate_path=config["gate"])
        repo = SQLiteExecutionRepository(runs, ActionCompiler(validator, (executor.binding,)))
        action = repo.get(config["action_id"])
        spec = ExecutionSpec.from_compiled(action.spec)
        if config.get("changed_payload"):
            spec = replace(spec, arguments={"service_id": "service-demo", "note": "different"})
        barrier("adapter-port-ready")
        outcome = await executor.execute(
            spec,
            action.effect_key,
            AttemptContext(str(uuid4()), asyncio.get_running_loop().time() + 5, asyncio.Event()),
        )
        emit(
            "adapter-port-result",
            category=outcome.category.value,
            receipt=outcome.receipt_reference,
            reason=outcome.no_effect_reason,
        )

    if config.get("adapter_port_test"):
        threading.Thread(target=control, daemon=True).start()
        asyncio.run(adapter_port_test())
        return 0

    try:
        with single_owner(config["gate"], predecessor_stopped=True):
            emit("owner")
            threading.Thread(target=control, daemon=True).start()
            asyncio.run(serve())
    except ActionError:
        emit("denied")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
