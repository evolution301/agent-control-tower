"""A late settlement must never reopen a task a person already closed.

0.18.2 (T-0023): the task was accepted by a person, then a still-running async
turn settled and the worker overwrote it — `accepted` -> `reported`, triage
`reworked`. The acceptance record is the one that wins.
"""

import json
from types import SimpleNamespace

import pytest

from control_tower import worker
from control_tower.adapters.base import AdapterResult
from control_tower.board import (
    Board,
    STATUS_ACCEPTED,
    STATUS_CLAIMED,
    STATUS_IN_PROGRESS,
)
from control_tower.config import ensure_layout, parse_config
from control_tower.dispatch import dispatch

REPORT = """## Status
ready

## Summary
done

## Files
- none

## Checks
- none
"""


class _Adapter:
    """Returns a report; `on_run` lets a test accept the task mid-turn."""

    name = "stub"

    def __init__(self):
        self.on_run = None
        self.calls = 0

    def run(self, req):
        self.calls += 1
        if self.on_run:
            self.on_run()
        return AdapterResult(text=REPORT, exit_code=0)


@pytest.fixture()
def tower(tmp_path, monkeypatch):
    config = {
        "name": "settle-tower",
        "workspace": str(tmp_path / "tower"),
        "workers": 1,
        "roles": {"qa": {"description": "qa", "adapter": "mock"}},
        "adapters": {"dsh": {"base_url": "http://127.0.0.1:1"}},
    }
    path = tmp_path / "tower.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    cfg = parse_config(path)
    ensure_layout(cfg)
    adapter = _Adapter()
    monkeypatch.setattr("control_tower.dispatch.get_adapter", lambda name, opts: adapter)
    return SimpleNamespace(cfg=cfg, adapter=adapter)


def _ledger(cfg):
    if not cfg.ledger_path.exists():
        return []
    return [json.loads(line) for line in cfg.ledger_path.read_text(encoding="utf-8").splitlines()]


def _accepted(board):
    task = board.create("qa", "ship the pages")
    board.transition(task, STATUS_ACCEPTED, note="human accepted the report")
    return board.load(task.id)


def test_a_settlement_after_acceptance_leaves_the_task_accepted(tower):
    cfg, board = tower.cfg, Board(tower.cfg)
    task = _accepted(board)
    worker.enqueue(cfg, "dispatch", task.id)

    assert worker.run_worker(cfg, once=True) == 0

    settled = board.load(task.id)
    assert settled.status == STATUS_ACCEPTED, "the acceptance record wins"
    assert settled.settled_at == "", "an ignored settlement does not stamp settled_at"
    assert tower.adapter.calls == 0, "no turn should run for a closed task"
    ignored = [line for line in _ledger(cfg) if line["event"] == "settle_ignored"]
    assert ignored and ignored[-1]["status"] == STATUS_ACCEPTED
    assert ignored[-1].get("job"), "the ignored settlement names its job"
    notices = [item for item in board.inbox() if item.get("event") == "async_settled"]
    assert notices and "informational" in notices[-1]["summary"]


def test_a_report_that_lands_after_acceptance_becomes_history_only(tower):
    cfg, board = tower.cfg, Board(tower.cfg)
    task = board.create("qa", "ship the pages")
    # the person accepts while the turn is still running
    tower.adapter.on_run = lambda: board.transition(
        board.load(task.id), STATUS_ACCEPTED, note="human accepted mid-turn")

    out = dispatch(cfg, board, task)

    assert out.status == STATUS_ACCEPTED
    late = board.load(task.id).escalation["late_report"]
    assert late["status"] == STATUS_ACCEPTED
    assert late["report"]["summary"] == "done", "the report is kept for the audit trail"
    assert board.load(task.id).report is None, "the late report did not replace the record"
    ignored = [line for line in _ledger(cfg) if line["event"] == "settle_ignored"]
    assert ignored and ignored[-1]["phase"] == "post"


def test_a_task_that_is_still_running_settles_normally(tower):
    cfg, board = tower.cfg, Board(tower.cfg)
    task = board.create("qa", "ship the pages")
    task.max_rounds = 0                    # no rework re-dispatch: one turn, one settlement
    board.save(task)
    board.transition(task, STATUS_CLAIMED, note="dispatched")
    board.transition(board.load(task.id), STATUS_IN_PROGRESS, note="turn running")
    worker.enqueue(cfg, "dispatch", task.id)

    assert worker.run_worker(cfg, once=True) == 0

    settled = board.load(task.id)
    assert settled.status != STATUS_IN_PROGRESS, "a live turn is settled, not ignored"
    assert settled.settled_at, "a normal settlement is stamped"
    assert tower.adapter.calls == 1
    assert not [line for line in _ledger(cfg) if line["event"] == "settle_ignored"]


def test_cancelling_jobs_leaves_nothing_queued_for_a_closed_task(tower):
    cfg, board = tower.cfg, Board(tower.cfg)
    task = _accepted(board)
    first = worker.enqueue(cfg, "dispatch", task.id)
    second = worker.enqueue(cfg, "restate", task.id)

    assert worker.cancel_jobs(cfg, task.id) == 2
    assert worker.queue_depth(cfg) == 0
    done = {path.stem: json.loads(path.read_text(encoding="utf-8"))
            for path in (cfg.queue_dir / "done").glob("*.json")}
    assert done[first["id"]]["result"]["outcome"] == "cancelled"
    assert done[second["id"]]["result"]["outcome"] == "cancelled"
    assert [line for line in _ledger(cfg) if line["event"] == "jobs_cancelled"]


def test_a_restate_job_for_a_closed_task_is_ignored(tower):
    cfg, board = tower.cfg, Board(tower.cfg)
    task = _accepted(board)
    worker.enqueue(cfg, "restate", task.id)

    assert worker.run_worker(cfg, once=True) == 0

    assert board.load(task.id).status == STATUS_ACCEPTED
    assert tower.adapter.calls == 0
    assert [line for line in _ledger(cfg) if line["event"] == "settle_ignored"]
