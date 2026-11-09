"""Issues bot flow: auto-escalation, attention lifecycle, secretary chain (v0.12)."""

import json
import pathlib

import pytest

from control_tower import cli, worker
from control_tower.board import Board
from control_tower.config import ensure_layout, parse_config
from control_tower.snapshot import build_snapshot
from control_tower.triage import triage_task


@pytest.fixture()
def tower(tmp_path, monkeypatch):
    config = {
        "name": "issues-flow",
        "workspace": str(tmp_path),
        "roles": {
            "seo": {"description": "s", "adapter": "mock", "max_rounds": 1},
            "issues": {"description": "问题处理", "adapter": "mock", "max_rounds": 1},
            "deputy": {"description": "项目秘书", "adapter": "mock", "max_rounds": 1},
            "planner": {"description": "总控", "adapter": "mock", "max_rounds": 1},
        },
        "escalation": {"enabled": True, "role": "issues", "max_attempts": 1},
    }
    path = tmp_path / "tower.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    cfg = parse_config(path)
    ensure_layout(cfg)
    monkeypatch.chdir(tmp_path)
    return cfg


def _exhausted_failure(tower, board):
    task = board.create("seo", "broken job")
    task.round = task.max_rounds          # the retry budget is spent
    task.status = "failed"
    task.history.append({"ts": "2026-11-09T10:00:00+0800", "status": "failed",
                         "note": "adapter died", "output": "boom"})
    board.save(task)
    return task


def _queue_jobs(tower):
    pending = tower.queue_dir / "pending"
    return [json.loads(p.read_text(encoding="utf-8"))
            for p in pending.glob("*.json")] if pending.exists() else []


def test_auto_escalation_delivers_to_issues_and_is_idempotent(tower):
    board = Board(tower)
    task = _exhausted_failure(tower, board)

    result = triage_task(tower, board, task)
    assert result["outcome"] == "escalated"
    reloaded = board.load(task.id)
    escalation = reloaded.escalation
    assert escalation["role"] == "issues"
    assert escalation["issues_task"]
    assert reloaded.state == "blocked"
    # the issues task exists and its dispatch job is queued for the worker
    issues_task = board.load(escalation["issues_task"])
    assert issues_task.role == "issues"
    assert f"来源任务：{task.id}" in issues_task.brief
    assert any(j.get("task") == issues_task.id for j in _queue_jobs(tower))

    # idempotent: a second pass must not create a second issues task
    before = len([t for t in board.all() if t.role == "issues"])
    triage_task(tower, board, task)
    after = len([t for t in board.all() if t.role == "issues"])
    assert after == before


def test_claimed_attention_leaves_needs_person_and_counts_as_processing(tower):
    board = Board(tower)
    task = _exhausted_failure(tower, board)
    triage_task(tower, board, task)
    task = board.load(task.id)
    assert {t.id for t in board.needs_person()} == {task.id}

    board.claim_attention(task, by="issues", note="triaging")
    reloaded = board.load(task.id)
    assert reloaded.attention["status"] == "claimed"
    assert board.needs_person() == [], "claimed work is 处理中, not 需要你"
    snap = build_snapshot(tower)
    assert snap.counts["attention"] == 0
    assert snap.counts["attention_claimed"] == 1


def test_claim_and_resolve_cli_lifecycle(tower, capsys):
    board = Board(tower)
    task = _exhausted_failure(tower, board)
    triage_task(tower, board, task)

    assert cli.main(["attention", "claim", task.id, "--by", "issues", "--note", "take"]) == 0
    assert "CLAIMED" in capsys.readouterr().out
    assert cli.main(["attention", "resolve", task.id, "--as", "dropped", "--note", "外部额度"]) == 0
    out = capsys.readouterr().out
    assert "dropped" in out

    reloaded = board.load(task.id)
    assert reloaded.status == "failed"
    assert reloaded.attention["status"] == "resolved"
    assert reloaded.attention["resolution"] == "dropped"
    assert board.needs_person() == []
    # a late triage pass must not re-raise the resolved issue
    assert triage_task(tower, board, reloaded)["outcome"] == "resolved_hold"


def test_resolve_as_rework_reopens_the_normal_flow(tower):
    board = Board(tower)
    task = _exhausted_failure(tower, board)
    triage_task(tower, board, task)
    assert cli.main(["attention", "resolve", task.id, "--as", "rework", "--note", "retry"]) == 0
    reloaded = board.load(task.id)
    assert reloaded.status == "rework"
    assert not reloaded.attention, "a fresh attempt is a fresh issue"


def test_escalate_command_writes_the_secretary_chain(tower, capsys):
    board = Board(tower)
    task = _exhausted_failure(tower, board)
    triage_task(tower, board, task)

    assert cli.main(["escalate", task.id, "--to", "deputy", "--note", "需要拍板"]) == 0
    out = capsys.readouterr().out
    assert "deputy" in out
    reloaded = board.load(task.id)
    assert reloaded.escalation["chain"] == ["deputy"]
    deputy_tasks = [t for t in board.all() if t.role == "deputy"]
    assert len(deputy_tasks) == 1
    assert "问题：需要拍板" in deputy_tasks[0].brief or f"来源任务：{task.id}" in deputy_tasks[0].brief


def test_escalation_disabled_keeps_the_machine_quiet(tmp_path, monkeypatch):
    config = {
        "name": "quiet",
        "workspace": str(tmp_path),
        "roles": {"seo": {"description": "s", "adapter": "mock", "max_rounds": 1},
                  "issues": {"description": "i", "adapter": "mock"}},
        "escalation": {"enabled": False, "role": "issues"},
    }
    path = tmp_path / "tower.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    cfg = parse_config(path)
    ensure_layout(cfg)
    monkeypatch.chdir(tmp_path)

    board = Board(cfg)
    task = _exhausted_failure(cfg, board)
    result = triage_task(cfg, board, task)
    assert result["outcome"] == "escalated"
    assert board.load(task.id).escalation in ({}, None)
    assert not [t for t in board.all() if t.role == "issues"]
