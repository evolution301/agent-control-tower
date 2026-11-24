"""T-0023: a long turn must be able to say "still working" without being judged.

Field report (aimigao-zh, 2026-11-24): a 38-minute verification was judged "no
report" at 17:05 while its report landed 17:13. These pins keep a progress report
in the waiting state (extended due time, never settled, never reworked) and pin
the half-deadline nudge that asks for one.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from control_tower import dispatch as dispatch_mod
from control_tower import worker
from control_tower.adapters.base import AdapterResult
from control_tower.board import Board, STATUS_IN_PROGRESS, STATUS_REPORTED
from control_tower.config import parse_config
from control_tower.dispatch import (
    PROGRESS_HINT,
    dispatch,
    is_progress_report,
    parse_report,
    progress_window_notice,
    record_progress,
    render_prompt,
)

PROGRESS_TEXT = """## Status
progress

## Summary
40 of 120 pages verified; 25 minutes left on the pass.

## Files
- none

## Checks
- none
"""

REAL_TEXT = """## Status
ready

## Summary
All 120 pages verified.

## Files
- roles/qa/REPORT.md — the pass

## Checks
- python -m pytest -q → 12 passed
"""


class _TextAdapter:
    name = "mock"

    def __init__(self, text: str, exit_code: int = 0) -> None:
        self.text = text
        self.exit_code = exit_code

    def run(self, req):  # noqa: ANN001 - duck typed on purpose
        return AdapterResult(exit_code=self.exit_code, text=self.text)


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
            "adapters:",
            "  dsh:",
            '    base_url: "http://127.0.0.1:1"',
        ]) + "\n",
        encoding="utf-8",
    )
    return parse_config(path)


def _ledger(cfg):
    return [json.loads(line) for line in cfg.ledger_path.read_text(encoding="utf-8").splitlines()]


def test_a_progress_report_extends_the_window_and_keeps_working(tmp_path, monkeypatch):
    cfg = _tower(tmp_path)
    board = Board(cfg)
    task = board.create("qa", "verify 120 pages")
    started = datetime.strptime(task.created_at[:19], "%Y-%m-%dT%H:%M:%S")
    monkeypatch.setattr(dispatch_mod, "get_adapter", lambda name, options: _TextAdapter(PROGRESS_TEXT))

    out = dispatch(cfg, board, task)

    assert out.status == STATUS_IN_PROGRESS, "a heartbeat must not settle the task"
    assert out.state == "progress"
    progress = out.escalation["progress"]
    given = datetime.strptime(progress["due_before"], "%Y-%m-%dT%H:%M:%S")
    extended = datetime.strptime(progress["due_after"], "%Y-%m-%dT%H:%M:%S")
    assert given - started == timedelta(hours=4), "the given window is 4h"
    assert extended - given == timedelta(hours=4), "one slice = the window it was given"
    assert task.due_at == progress["due_after"]
    assert progress["seen"] == 1
    assert out.settled_at == "", "nothing settled"

    again = record_progress(board, task, parse_report(PROGRESS_TEXT))
    assert again["seen"] == 2
    assert (datetime.strptime(again["due_after"], "%Y-%m-%dT%H:%M:%S")
            - datetime.strptime(again["due_before"], "%Y-%m-%dT%H:%M:%S")
            == timedelta(hours=4)), "a second heartbeat buys the same slice, not twice it"
    assert [line["event"] for line in _ledger(cfg)].count("progress_report") == 2


def test_an_empty_answer_is_progress_not_a_failure(tmp_path, monkeypatch):
    cfg = _tower(tmp_path)
    board = Board(cfg)
    monkeypatch.setattr(dispatch_mod, "get_adapter", lambda name, options: _TextAdapter(""))

    out = dispatch(cfg, board, board.create("qa", "x"))

    assert out.status == STATUS_IN_PROGRESS
    assert out.state == "progress"
    assert is_progress_report(parse_report("")), "an empty answer proves nothing either way"


def test_the_worker_does_not_settle_a_progress_report(tmp_path, monkeypatch):
    cfg = _tower(tmp_path)
    board = Board(cfg)
    task = board.create("qa", "verify 120 pages")
    task.mode = "async"
    board.save(task)
    monkeypatch.setattr(dispatch_mod, "get_adapter", lambda name, options: _TextAdapter(PROGRESS_TEXT))

    result = worker._settle_dispatch(cfg, board, {"task": task.id, "kind": "dispatch"})

    reloaded = board.load(task.id)
    assert result["outcome"] == "progress"
    assert reloaded.status == STATUS_IN_PROGRESS
    assert reloaded.settled_at == "", "the waiting window stays open"
    assert reloaded.notified is False, "no settlement notice"
    assert board.inbox()[-1]["event"] == "async_progress"


def test_a_real_report_still_files_and_verifies(tmp_path, monkeypatch):
    cfg = _tower(tmp_path)
    board = Board(cfg)
    monkeypatch.setattr(dispatch_mod, "get_adapter", lambda name, options: _TextAdapter(REAL_TEXT))

    out = dispatch(cfg, board, board.create("qa", "x"))

    assert out.status == STATUS_REPORTED
    assert out.state == "ready"
    assert not is_progress_report(out.report)


def test_the_half_deadline_nudge_asks_once(tmp_path):
    cfg = _tower(tmp_path)
    board = Board(cfg)
    task = board.create("qa", "verify 120 pages")
    start = datetime.now().replace(microsecond=0)
    task.status = STATUS_IN_PROGRESS
    task.due_at = (start + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
    board.save(task)

    assert progress_window_notice(cfg, board, now=start + timedelta(minutes=20)) == []
    fired = progress_window_notice(cfg, board, now=start + timedelta(minutes=31))

    assert [item["task"] for item in fired] == [task.id]
    hint = cfg.task_dir(task.id) / "progress-hint.md"
    assert PROGRESS_HINT.format(task=task.id) in hint.read_text(encoding="utf-8")
    assert "the tower will keep waiting" in hint.read_text(encoding="utf-8")
    assert board.inbox()[-1]["event"] == "progress_hint"
    assert board.load(task.id).escalation["half_note_at"]
    assert progress_window_notice(cfg, board, now=start + timedelta(minutes=50)) == [], "one shot"


def test_a_filed_progress_report_silences_the_half_deadline_nudge(tmp_path):
    cfg = _tower(tmp_path)
    board = Board(cfg)
    task = board.create("qa", "verify 120 pages")
    start = datetime.now().replace(microsecond=0)
    task.status = STATUS_IN_PROGRESS
    task.due_at = (start + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
    board.save(task)
    record_progress(board, task, parse_report(PROGRESS_TEXT))

    assert progress_window_notice(cfg, board, now=start + timedelta(minutes=45)) == []
    assert len(_ledger(cfg)) == 2, "task_created + progress_report, nothing else"


def test_every_lane_is_told_it_may_file_a_progress_report(tmp_path):
    cfg = _tower(tmp_path)
    task = Board(cfg).create("qa", "verify 120 pages")

    for compact in (False, True):
        prompt = render_prompt(cfg, task, compact=compact)
        assert "Status: progress" in prompt
        assert "keeps waiting" in prompt
