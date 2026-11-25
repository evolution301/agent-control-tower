"""T-0030 P0-A: the async pool is real, and one workspace runs one consumer.

The customer saw `claimed 2` with a single turn running and a second worker
silently idling. These tests pin the three things that make that visible: a job
span log whose intervals prove overlap, a startup banner naming the pool, and a
lock refusal that says who already owns the slot.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from control_tower import locks, worker
from control_tower.adapters.base import AdapterResult
from control_tower.board import Board
from control_tower.config import parse_config

REPORT_TEXT = """## Status
ready

## Summary
both jobs ran

## Files
- src/app.ts — untouched probe

## Checks
- python -m pytest -q → passed
"""


class _SlowAdapter:
    """Long enough that two turns overlap if nothing serialises them."""

    name = "mock"

    def __init__(self, hold_s: float = 0.3) -> None:
        self.hold_s = hold_s

    def run(self, req):
        time.sleep(self.hold_s)
        return AdapterResult(exit_code=0, text=REPORT_TEXT)


@pytest.fixture(autouse=True)
def no_detached_worker(monkeypatch):
    """A real `tower worker` must never be spawned by these tests."""
    monkeypatch.setattr(worker, "ensure_worker", lambda cfg: False)


def _tower(tmp_path, *, workers: int = 2):
    workspace = tmp_path / "tower"
    workspace.mkdir(exist_ok=True)
    path = tmp_path / "tower.toml"
    path.write_text(
        "\n".join([
            "name: runtime",
            f"workspace: {workspace.as_posix()}",
            f"workers: {workers}",
            "roles:",
            "  qa:",
            '    description: "qa"',
            "    adapter: mock",
            "    timeout_s: 30",
            "  fe:",
            '    description: "frontend"',
            "    adapter: mock",
            "    timeout_s: 30",
            "adapters:",
            "  dsh:",
            '    base_url: "http://127.0.0.1:1"',
        ]) + "\n",
        encoding="utf-8",
    )
    return parse_config(path)


def _two_jobs(cfg):
    board = Board(cfg)
    ids = []
    for role in ("qa", "fe"):
        task = board.create(role, "concurrency probe")
        worker.enqueue(cfg, "dispatch", task.id)
        ids.append(task.id)
    return ids


def _spans(cfg):
    path = cfg.state_dir / "jobs.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _fake_adapter(monkeypatch, adapter) -> None:
    monkeypatch.setattr("control_tower.dispatch.get_adapter", lambda name, options: adapter)


def test_pool_two_runs_two_roles_at_once(tmp_path, monkeypatch):
    cfg = _tower(tmp_path, workers=2)
    ids = _two_jobs(cfg)
    _fake_adapter(monkeypatch, _SlowAdapter())

    assert worker.run_worker(cfg, once=True) == 0

    spans = sorted(_spans(cfg), key=lambda span: span["started_ts"])
    assert len(spans) == 2
    assert {span["task"] for span in spans} == set(ids)
    assert {span["role"] for span in spans} == {"qa", "fe"}
    assert spans[0]["ended_ts"] >= spans[1]["started_ts"], (
        f"jobs ran one after the other: {spans}"
    )


def test_pool_one_keeps_them_sequential(tmp_path, monkeypatch):
    """The span log measures real overlap, not two timestamps written at once."""
    cfg = _tower(tmp_path, workers=1)
    _two_jobs(cfg)
    _fake_adapter(monkeypatch, _SlowAdapter(hold_s=0.2))

    assert worker.run_worker(cfg, once=True) == 0

    spans = sorted(_spans(cfg), key=lambda span: span["started_ts"])
    assert len(spans) == 2
    assert spans[0]["ended_ts"] <= spans[1]["started_ts"], (
        f"a pool of one overlapped: {spans}"
    )


def test_concurrency_flag_and_banner(tmp_path, capsys):
    cfg = _tower(tmp_path, workers=1)

    assert worker.run_worker(cfg, once=True, concurrency=3) == 0

    out = capsys.readouterr().out
    assert f"worker: pool=3 pid={os.getpid()}" in out
    assert "base_url=http://127.0.0.1:1" in out
    assert f"workspace={cfg.workspace}" in out


def test_second_worker_refuses_the_busy_slot(tmp_path, capsys):
    cfg = _tower(tmp_path)
    holder = worker._take_worker_lock(cfg)
    assert holder is not None
    try:
        assert worker.run_worker(cfg, once=True) == 1
    finally:
        locks.release_lock(holder)

    out = capsys.readouterr().out
    assert f"worker: another worker (pid {os.getpid()}" in out
    assert "holds .tower/worker.lock — jobs are already being consumed" in out


def test_status_names_the_pool_and_the_lock_holder(tmp_path):
    cfg = _tower(tmp_path, workers=2)
    holder = worker._take_worker_lock(cfg)
    try:
        worker._POOL = 2
        worker._heartbeat(cfg)
        status = worker.worker_status(cfg)
        assert status["pool"] == 2
        assert status["pid_holds_lock"] is True
        assert status["lock_holder"]["pid"] == os.getpid()
    finally:
        locks.release_lock(holder)

    assert worker.worker_status(cfg)["pid_holds_lock"] is False
