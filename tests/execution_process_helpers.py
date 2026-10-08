"""Bounded parent-owned handles; no PID searches, user processes or recursive deletion."""

import json
import os
import queue
import socket
import subprocess
import sys
import threading
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
REPORT_LOCK = threading.Lock()


def record_fact(kind, **values):
    report = os.environ.get("P5_TEST_PROCESS_REPORT")
    if report:
        with REPORT_LOCK, Path(report).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"kind": kind, **values}) + "\n")


class OwnedWorker:
    def __repr__(self):
        return "<owned test process; authority omitted>"

    def __init__(self, env, *, window="", ready=True, **options):
        self.nonce, self.phase = str(uuid4()), None
        self.window, self.closed, self.killed = window, False, False
        self.storage = env.tmp_path.resolve()
        assert self.storage != ROOT and not self.storage.is_relative_to(ROOT)
        self.env, self.events = env, queue.Queue()
        self.captured = []
        config = dict(
            nonce=self.nonce,
            gate=str(env.runs.path),
            demo=str(env.demo_path),
            scope=env.action.spec.execution_scope,
            token=env.token,
            action_id=env.action.action_id,
            journal=str(self.storage / "calls.jsonl"),
            window=window,
            **options,
        )
        environment = os.environ.copy()
        environment["PYTHONPATH"] = os.pathsep.join(
            [str(ROOT / "src"), *[p for p in sys.path if p.endswith("site-packages")]]
        )
        # Windows venv python.exe can be a launcher with a different child PID.
        # Spawn the actual base interpreter so the retained handle IS the fault owner.
        self.process = subprocess.Popen(
            [sys._base_executable, str(ROOT / "tests" / "execution_fault_worker.py")],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            cwd=ROOT,
            env=environment,
        )

        def read():
            for line in self.process.stdout:
                assert self.env.token not in line
                self.captured.append(json.loads(line))
                self.events.put(json.loads(line))

        self.reader = threading.Thread(target=read)
        self.reader.start()
        self.process.stdin.write(json.dumps(config) + "\n")
        self.process.stdin.flush()
        if ready:
            try:
                self.await_phase("ready")
            except BaseException:
                self.close()
                raise

    def await_phase(self, expected):
        while True:
            try:
                event = self.events.get(timeout=12)
            except queue.Empty:
                raise AssertionError("Owned worker did not reach requested phase") from None
            assert event["nonce"] == self.nonce and event["pid"] == self.process.pid
            self.phase = event["phase"]
            if self.phase == "ready":
                self.port = event["port"]
                self.client = httpx.Client(
                    base_url=f"http://127.0.0.1:{self.port}", timeout=10, trust_env=False
                )
            if self.phase == expected:
                return event
            assert self.phase != "denied", "Competing owner was denied"

    def control(self, operation):
        self.process.stdin.write(json.dumps({"operation": operation}) + "\n")
        self.process.stdin.flush()

    def kill_at(self, phase):
        assert self.phase == phase and self.process.poll() is None
        assert self.storage.is_dir() and not self.storage.is_relative_to(ROOT)
        self.process.kill()  # Only the retained child handle, Windows TerminateProcess.
        self.killed = True
        self.process.wait(timeout=5)
        self.close()

    def request(self, path, *, body=None, key=None, authenticated=True):
        headers = {"Authorization": "Bearer " + self.env.token} if authenticated else {}
        if key is not None:
            headers["Idempotency-Key"] = key
        try:
            response = self.client.request(
                "GET" if body is None else "POST", path, headers=headers, json=body
            )
            try:
                body = response.json()
            except ValueError:
                body = {"transport": "non-json-response"}
            return response.status_code, body
        except httpx.HTTPError:
            return 0, {"transport": "response-unavailable"}

    def close(self):
        if self.closed:
            return
        if self.process.poll() is None:
            try:
                self.control("stop")
                self.process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                self.process.kill()
                self.process.wait(timeout=5)
        self.process.stdin.close()
        self.reader.join(timeout=3)
        assert not self.reader.is_alive() and self.process.poll() is not None
        self.process.stdout.close()
        if hasattr(self, "client"):
            self.client.close()
            with socket.socket() as probe:
                probe.settimeout(0.2)
                assert probe.connect_ex(("127.0.0.1", self.port)) != 0
        self.closed = True
        record_fact(
            "process",
            nonce=self.nonce,
            pid=self.process.pid,
            window=self.window,
            terminated_at_barrier=self.killed,
            exit_code=self.process.returncode,
            storage=str(self.storage),
            port=getattr(self, "port", None),
            stopped=True,
            reader_joined=True,
            listener_closed=True,
            token_exposed=False,
            events=self.captured,
        )

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
