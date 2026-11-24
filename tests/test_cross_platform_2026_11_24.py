"""T-0027: the three runtime defects an external audit found in 0.18.3.

* `locks.py` imported `msvcrt` unconditionally, so a POSIX host could not import
  it although the docstring promises fcntl there;
* with `workers: 4` the pool supervisor's bare heartbeat erased the job a thread
  was running about 0.2 s after it was written, so `tower worker --status` never
  showed the in-flight job;
* `serial_requests` never engaged for async work because no enqueue filled
  `payload["role"]`, so one role could run two turns in the same session at once.
"""

from __future__ import annotations

import importlib
import json
import sys
import threading
import time
import types

import pytest

from control_tower import locks, worker
from control_tower.adapters.base import AdapterResult
from control_tower.board import Board
from control_tower.config import parse_config

REPORT_TEXT = """## Status
ready

## Summary
both jobs ran
"""


class _GateAdapter:
    """Holds every turn until released, and records how many ran at once."""

    name = "mock"

    def __init__(self, release: threading.Event | None = None) -> None:
        self.release = release or threading.Event()
        self.calls: list[str] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self._guard = threading.Lock()

    def run(self, req):
        with self._guard:
            self.calls.append(req.task_id)
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            self.release.wait(20)
        finally:
            with self._guard:
                self.in_flight -= 1
        return AdapterResult(exit_code=0, text=REPORT_TEXT)


class _SlowAdapter:
    """Sleeps long enough for two turns of one role to overlap if nothing serialises them."""

    name = "mock"

    def __init__(self, hold_s: float = 0.2) -> None:
        self.hold_s = hold_s
        self.calls: list[str] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self._guard = threading.Lock()

    def run(self, req):
        with self._guard:
            self.calls.append(req.task_id)
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            time.sleep(self.hold_s)
        finally:
            with self._guard:
                self.in_flight -= 1
        return AdapterResult(exit_code=0, text=REPORT_TEXT)


@pytest.fixture(autouse=True)
def no_detached_worker(monkeypatch):
    """A real `tower worker` must never be spawned by these tests."""
    monkeypatch.setattr(worker, "ensure_worker", lambda cfg: False)


def _tower(tmp_path, *, workers: int = 4, serial: bool = False):
    workspace = tmp_path / "tower"
    workspace.mkdir(exist_ok=True)
    path = tmp_path / "tower.toml"
    path.write_text(
        "\n".join([
            "name: runtime",
            f"workspace: {workspace.as_posix()}",
            f"workers: {workers}",
            f"serial_requests: {'true' if serial else 'false'}",
            "roles:",
            "  qa:",
            '    description: "qa"',
            "    adapter: mock",
            "    timeout_s: 30",
            "adapters:",
            "  dsh:",
            '    base_url: "http://127.0.0.1:1"',
        ]) + "\n",
        encoding="utf-8",
    )
    return parse_config(path)


def _fake_adapter(monkeypatch, adapter) -> None:
    monkeypatch.setattr("control_tower.dispatch.get_adapter", lambda name, options: adapter)


def _queue(cfg, brief: str):
    board = Board(cfg)
    task = board.create("qa", brief)
    job = worker.enqueue(cfg, "dispatch", task.id)
    return board, task, job


def test_posix_lock_backend_locks_and_unlocks(tmp_path):
    calls: list[int] = []
    fake_fcntl = types.ModuleType("fcntl")
    fake_fcntl.LOCK_EX, fake_fcntl.LOCK_NB, fake_fcntl.LOCK_UN = 2, 4, 8
    fake_fcntl.flock = lambda fd, flags: calls.append(flags)
    saved = {name: sys.modules.get(name) for name in ("msvcrt", "fcntl")}
    sys.modules["msvcrt"] = None               # a POSIX host has no msvcrt
    sys.modules["fcntl"] = fake_fcntl
    try:
        importlib.reload(locks)
        cfg = types.SimpleNamespace(state_dir=tmp_path)

        with locks.tower_lock(cfg, timeout_s=1):
            calls.append(0)
        assert calls == [fake_fcntl.LOCK_EX | fake_fcntl.LOCK_NB, 0, fake_fcntl.LOCK_UN]
        assert (tmp_path / ".lock").exists()

        def _busy(fd, flags):
            raise OSError("held elsewhere")

        fake_fcntl.flock = _busy
        with pytest.raises(TimeoutError, match="held for over"), \
                locks.tower_lock(cfg, timeout_s=0.05, poll_s=0.01):
            pass
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        importlib.reload(locks)


def test_pool_workers_keep_every_job_in_the_heartbeat(tmp_path, monkeypatch):
    cfg = _tower(tmp_path, workers=4)
    board, first, job_one = _queue(cfg, "first brief")
    _, second, job_two = _queue(cfg, "second brief")
    adapter = _GateAdapter()
    _fake_adapter(monkeypatch, adapter)

    runner = threading.Thread(target=worker.run_worker, args=(cfg, True), daemon=True)
    runner.start()
    try:
        deadline = time.time() + 10
        status: dict = {"jobs": []}
        while time.time() < deadline and len(status["jobs"]) < 2:
            status = worker.worker_status(cfg)
            time.sleep(0.05)
        assert {job["id"] for job in status["jobs"]} == {job_one["id"], job_two["id"]}, (
            "the supervisor's bare heartbeat must not erase a running job")
        assert {job["task"] for job in status["jobs"]} == {first.id, second.id}
        assert {job["kind"] for job in status["jobs"]} == {"dispatch"}
        assert {job["role"] for job in status["jobs"]} == {"qa"}
        assert all(job["age_minutes"] is not None for job in status["jobs"])
        assert status["current_job"] in {job_one["id"], job_two["id"]}
        assert json.loads((cfg.queue_dir / "worker.json").read_text(encoding="utf-8"))["pid"]
    finally:
        adapter.release.set()
        runner.join(20)
    assert board.load(first.id).status in {"reported", "ready"}
    assert board.load(second.id).status in {"reported", "ready"}


def test_serial_requests_keeps_one_roles_turns_apart(tmp_path, monkeypatch):
    cfg = _tower(tmp_path, workers=4, serial=True)
    board, first, job_one = _queue(cfg, "first brief")
    _, second, _ = _queue(cfg, "second brief")
    adapter = _SlowAdapter()
    _fake_adapter(monkeypatch, adapter)

    assert job_one["payload"]["role"] == "qa", "enqueue records the role it knows"
    assert worker._job_role(cfg, {"task": first.id, "payload": {}}) == "qa", (
        "a job without payload.role still resolves it from the task file")

    assert worker.run_worker(cfg, once=True) == 0

    assert set(adapter.calls) == {first.id, second.id}
    assert adapter.max_in_flight == 1, "one role must never run two turns at once"
    assert board.load(first.id).status in {"reported", "ready"}
    assert board.load(second.id).status in {"reported", "ready"}
