"""T-0030 P1-C: a timeout is not a verdict while the turn is still running.

The customer's T-0240 was marked failed at 11:05 by the 30-minute adapter
timeout and kept writing artifacts until 11:42. A timed-out turn is now `late`:
cancel it best-effort, then let supervise promote it when the report lands or
fail it at a hard horizon.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from control_tower import cli
from control_tower.adapters.base import AdapterResult
from control_tower.board import Board
from control_tower.config import parse_config
from control_tower.dispatch import (
    LATE_HORIZON_S,
    LATE_NOTE,
    LATE_STATUS,
    dispatch,
    recheck_late,
)

TIMEOUT_TEXT = "[dsh] timed out after 30s waiting for T-0240-REPORT.md (turns=52)"

GOOD_REPORT = """## Status
ready

## Summary
the turn finished after the timeout

## Files
- src/app.ts — fixed

## Checks
- npm test → 4 passed
"""


class _TimeoutAdapter:
    name = "mock"

    def __init__(self) -> None:
        self.cancelled: list[str] = []

    def run(self, req):
        return AdapterResult(exit_code=4, text=TIMEOUT_TEXT, meta={"session_id": "s-1"})

    def cancel(self, session_id: str) -> None:
        self.cancelled.append(session_id)


def _tower(tmp_path):
    workspace = tmp_path / "tower"
    workspace.mkdir(exist_ok=True)
    path = tmp_path / "tower.toml"
    path.write_text(
        "\n".join([
            "name: runtime",
            f"workspace: {workspace.as_posix()}",
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
    return path, parse_config(path)


def _ledger(cfg, event: str) -> list[dict]:
    path = cfg.state_dir / "ledger.jsonl"
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return [row for row in rows if row.get("event") == event]


def _timed_out_task(tmp_path, monkeypatch):
    path, cfg = _tower(tmp_path)
    adapter = _TimeoutAdapter()
    monkeypatch.setattr("control_tower.dispatch.get_adapter", lambda name, options: adapter)
    board = Board(cfg)
    task = board.create("qa", "a turn that outlives its timeout")
    dispatch(cfg, board, task)
    return path, cfg, board, task, adapter


def test_timeout_marks_late_and_cancels_the_session(tmp_path, monkeypatch):
    _, cfg, board, task, adapter = _timed_out_task(tmp_path, monkeypatch)

    task = board.load(task.id)
    assert task.status == LATE_STATUS
    assert task.escalation["late"]["cancelled"] is True
    assert adapter.cancelled == ["s-1"]
    assert [row["note"] for row in task.history][-1] == LATE_NOTE
    assert _ledger(cfg, "adapter_timeout")[-1]["cancelled"] is True


def test_a_report_landing_late_promotes_to_reported(tmp_path, monkeypatch):
    _, cfg, board, task, _ = _timed_out_task(tmp_path, monkeypatch)
    report_path = cfg.role_dir(task.role) / f"{task.id}-REPORT.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(GOOD_REPORT, encoding="utf-8")

    results = recheck_late(cfg, board)

    assert results == [{"task": task.id, "outcome": "recovered"}]
    task = board.load(task.id)
    assert task.status == "reported"
    assert task.report["summary"] == "the turn finished after the timeout"
    assert _ledger(cfg, "late_recovered")[-1]["task"] == task.id


def test_the_hard_horizon_fails_a_late_task(tmp_path, monkeypatch):
    _, cfg, board, task, _ = _timed_out_task(tmp_path, monkeypatch)

    assert recheck_late(cfg, board) == [{"task": task.id, "outcome": "still-late"}]

    late = datetime.now() + timedelta(seconds=LATE_HORIZON_S + 60)
    assert recheck_late(cfg, board, now=late) == [{"task": task.id, "outcome": "failed"}]
    assert board.load(task.id).status == "failed"
    assert _ledger(cfg, "late_failed")[-1]["task"] == task.id


def test_tower_status_shows_the_late_task(tmp_path, monkeypatch, capsys):
    path, _, _, task, _ = _timed_out_task(tmp_path, monkeypatch)

    assert cli.main(["--workspace", str(path), "status"]) == 0

    out = capsys.readouterr().out
    assert task.id in out
    assert "late" in out
