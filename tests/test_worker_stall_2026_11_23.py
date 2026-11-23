"""A restate job must never hold the whole async queue (field report 2026-11-23).

A dead target session made `_settle_restate` wait on the adapter turn forever:
Q-00212 was claimed and never executed, Q-00214 sat behind it, and every async
settlement starved for 40+ minutes while the heartbeat kept ticking.
"""

import json
import threading
import time

import pytest

from control_tower import worker
from control_tower.adapters import AdapterResult
from control_tower.board import Board
from control_tower.config import ensure_layout, parse_config


class FakeAdapter:
    name = "fake"

    def __init__(self, stall_task: str = "", started: threading.Event | None = None,
                 release: threading.Event | None = None):
        self.stall_task = stall_task
        self.started = started or threading.Event()
        self.release = release or threading.Event()
        self.calls: list[str] = []
        self.state: dict = {}

    def session_state(self, session_id: str) -> dict:
        return dict(self.state)

    def run(self, req) -> AdapterResult:
        self.calls.append(req.task_id)
        self.started.set()
        if req.task_id == self.stall_task:
            self.release.wait(30)
        return AdapterResult(text="## Restatement\nWe will do it.\n", exit_code=0)


def _tower(tmp_path, roles=None, sessions=None):
    config = {
        "name": "stall-tower",
        "workspace": str(tmp_path / "tower"),
        "roles": roles or {"core": {"description": "c", "adapter": "mock", "timeout_s": 1}},
        "adapters": {"dsh": {"base_url": "http://127.0.0.1:1", "sessions": sessions or {}}},
    }
    path = tmp_path / "tower.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    cfg = parse_config(path)
    ensure_layout(cfg)
    return cfg


def _fake(monkeypatch, adapter: FakeAdapter) -> None:
    monkeypatch.setattr("control_tower.dispatch.get_adapter", lambda name, opts: adapter)
    monkeypatch.setattr("control_tower.adapters.get_adapter", lambda name, opts: adapter)


def _restate(cfg, role: str, brief: str):
    board = Board(cfg)
    task = board.create(role, brief)
    task.mode = "async"
    board.save(task)
    job = worker.enqueue(cfg, "restate", task.id)
    return board, task, job


def test_restate_timeout_fails_the_job_and_the_next_one_runs(tmp_path, monkeypatch):
    cfg = _tower(tmp_path)
    board, stalled, job = _restate(cfg, "core", "first brief")
    _, second, _ = _restate(cfg, "core", "second brief")
    adapter = FakeAdapter(stall_task=stalled.id)
    _fake(monkeypatch, adapter)

    started = time.perf_counter()
    assert worker.run_worker(cfg, once=True) == 0
    elapsed = time.perf_counter() - started

    assert elapsed < 5.0, "the 1s budget bounds the job; the next job still runs"
    assert board.load(stalled.id).status == "failed"
    assert board.load(second.id).status == "restating", "the queue advanced"
    assert second.id in adapter.calls
    ledger = cfg.ledger_path.read_text(encoding="utf-8")
    assert '"event": "restate_timeout"' in ledger
    assert worker.queue_depth(cfg) == 0


def test_unavailable_target_fails_without_waiting(tmp_path, monkeypatch):
    cfg = _tower(tmp_path, roles={"core": {"description": "c", "adapter": "dsh",
                                           "timeout_s": 30}},
                 sessions={"core": "s-dead"})
    board, task, _ = _restate(cfg, "core", "brief")
    adapter = FakeAdapter()
    adapter.state = {"error": "projections unavailable: 429 too many requests"}
    _fake(monkeypatch, adapter)

    started = time.perf_counter()
    assert worker.run_worker(cfg, once=True) == 0
    elapsed = time.perf_counter() - started

    assert elapsed < 2.0, "a dead target must not reach the wait"
    assert adapter.calls == [], "the adapter turn was never started"
    assert board.load(task.id).status == "failed"
    assert "restate_target_unavailable" in cfg.ledger_path.read_text(encoding="utf-8")


def test_heartbeat_names_the_running_job(tmp_path, monkeypatch):
    cfg = _tower(tmp_path, roles={"core": {"description": "c", "adapter": "mock",
                                           "timeout_s": 30}})
    _, task, job = _restate(cfg, "core", "brief")
    started, release = threading.Event(), threading.Event()
    adapter = FakeAdapter(stall_task=task.id, started=started, release=release)
    _fake(monkeypatch, adapter)

    runner = threading.Thread(target=worker.run_worker, args=(cfg, True), daemon=True)
    runner.start()
    assert started.wait(5), "the job reached the adapter"
    info = json.loads((cfg.queue_dir / "worker.json").read_text(encoding="utf-8"))
    status = worker.worker_status(cfg)

    assert info["current_job"] == job["id"]
    assert info["current_kind"] == "restate"
    assert info["task"] == task.id
    assert isinstance(info["started_at"], float)
    assert status["current_job"] == job["id"]

    release.set()
    runner.join(10)
    assert not runner.is_alive()
    assert "current_job" not in json.loads(
        (cfg.queue_dir / "worker.json").read_text(encoding="utf-8"))
