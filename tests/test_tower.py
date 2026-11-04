"""Tests for the control tower core (offline: no model, no network)."""

import json
import sys

import pytest

from control_tower.board import Board, STATUS_REPORTED
from control_tower.config import ensure_layout, parse_config
from control_tower.dispatch import dispatch, parse_report, render_prompt
from control_tower.verify import verify_report


@pytest.fixture()
def tower(tmp_path, monkeypatch):
    config = {
        "name": "test-tower",
        "workspace": str(tmp_path),
        "roles": {
            "backend": {
                "description": "server side",
                "adapter": "mock",
                "write_scopes": ["roles/backend/"],
                "max_rounds": 2,
            },
            "qa": {"description": "verification", "adapter": "mock", "max_rounds": 1},
        },
        "verify_commands": [f'"{sys.executable}" -c "print(1)"'],
    }
    path = tmp_path / "tower.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    cfg = parse_config(path)
    ensure_layout(cfg)
    monkeypatch.chdir(tmp_path)
    return cfg


def test_config_lists_roles(tower):
    assert set(tower.roles) == {"backend", "qa"}
    assert tower.roles["backend"].adapter == "mock"
    assert tower.verify_commands


def test_task_ids_increment_and_board_roundtrip(tower):
    board = Board(tower)
    t1 = board.create("backend", "first")
    t2 = board.create("backend", "second")
    assert (t1.id, t2.id) == ("T-0001", "T-0002")
    t2.status = STATUS_REPORTED
    board.save(t2)
    assert board.load("T-0002").status == STATUS_REPORTED


def test_ledger_is_append_only_jsonl(tower):
    board = Board(tower)
    board.create("backend", "ledger test")
    lines = tower.ledger_path.read_text(encoding="utf-8").splitlines()
    assert lines
    events = [json.loads(line)["event"] for line in lines]
    assert "task_created" in events


def test_prompt_contains_contract_and_write_scope(tower):
    board = Board(tower)
    task = board.create("backend", "do the thing")
    prompt = render_prompt(tower, task)
    assert "You are the **backend** role" in prompt
    assert "roles/backend/" in prompt
    assert task.id in prompt
    assert "Never claim a check you did not run" in prompt


def test_dispatch_mock_produces_report_and_artifact(tower):
    board = Board(tower)
    task = board.create("backend", "produce artifact")
    task = dispatch(tower, board, task)
    assert task.status == STATUS_REPORTED
    assert task.report and task.report["summary"]
    assert task.report["files"], "mock adapter must report its artifact"
    assert task.report["checks"], "mock adapter must report real checks"
    assert (tower.state_dir / "inbox.jsonl").exists()


def test_verify_rejects_report_without_checks(tower):
    board = Board(tower)
    task = board.create("backend", "x")
    (tower.workspace / "roles" / "backend" / "f.txt").write_text("x", encoding="utf-8")
    task.report = {"summary": "did it", "files": ["roles/backend/f.txt"], "checks": []}
    result = verify_report(tower, task)
    assert not result.ok
    assert any("checks" in p for p in result.problems)


def test_verify_rejects_check_without_result(tower):
    board = Board(tower)
    task = board.create("backend", "x")
    (tower.workspace / "roles" / "backend" / "f.txt").write_text("x", encoding="utf-8")
    task.report = {"summary": "s", "files": ["roles/backend/f.txt"], "checks": ["pytest"]}
    result = verify_report(tower, task)
    assert not result.ok
    assert any("→" in p for p in result.problems)


def test_verify_rejects_missing_files(tower):
    board = Board(tower)
    task = board.create("backend", "x")
    task.report = {"summary": "s", "files": ["roles/backend/ghost.txt"], "checks": ["ls → ok"]}
    result = verify_report(tower, task)
    assert not result.ok
    assert any("do not exist" in p for p in result.problems)


def test_verify_accepts_good_report_and_runs_commands(tower):
    board = Board(tower)
    task = board.create("backend", "x")
    (tower.workspace / "roles" / "backend" / "f.txt").write_text("x", encoding="utf-8")
    task.report = {"state": "ready", "summary": "s", "files": ["roles/backend/f.txt"],
                   "checks": ["pytest → 9 passed"]}
    result = verify_report(tower, task)
    assert result.ok, result.problems
    assert result.ran and result.ran[0]["exit_code"] == 0


def test_parse_report_handles_markdown_sections():
    text = """## Summary
Did the work.

## Files
- `a.py` — added

## Checks
- `pytest` → 3 passed

## Open issues
- none
"""
    report = parse_report(text)
    assert report["summary"] == "Did the work."
    assert report["files"] == ["`a.py` — added"]
    assert report["checks"] == ["`pytest` → 3 passed"]
    assert report["open_issues"] == ["none"]


def test_inbox_mark_read(tower):
    board = Board(tower)
    task = board.create("backend", "x")
    dispatch(tower, board, task)
    assert board.inbox()
    assert board.mark_inbox_read() >= 1
    assert board.inbox(unread_only=True) == []


# ---------------------------------------------------------------- v0.2 structured state


def test_parse_report_reads_status_and_attention():
    text = """## Status
blocked

## Summary
Cannot proceed without the API key.

## Files
- none

## Checks
- `curl /health` → 401

## Attention
Which environment should I use, staging or production?
"""
    report = parse_report(text)
    assert report["state"] == "blocked"
    assert report["attention"]["question"].startswith("Which environment")


def test_verify_rejects_unknown_status(tower):
    board = Board(tower)
    task = board.create("backend", "x")
    (tower.workspace / "roles" / "backend" / "f.txt").write_text("x", encoding="utf-8")
    task.report = {"state": "done-ish", "summary": "s", "files": ["roles/backend/f.txt"],
                   "checks": ["pytest → ok"]}
    result = verify_report(tower, task)
    assert not result.ok
    assert any("unknown status" in p for p in result.problems)


def test_verify_requires_attention_when_blocked(tower):
    board = Board(tower)
    task = board.create("backend", "x")
    (tower.workspace / "roles" / "backend" / "f.txt").write_text("x", encoding="utf-8")
    task.report = {"state": "blocked", "summary": "s", "files": ["roles/backend/f.txt"],
                   "checks": ["pytest → failed"], "attention": None}
    result = verify_report(tower, task)
    assert not result.ok
    assert any("Attention" in p for p in result.problems)


def test_dispatch_records_state_attention_and_assignment(tower):
    board = Board(tower)
    task = board.create("backend", "produce artifact")
    task = dispatch(tower, board, task)
    assert task.state == "ready"
    assert task.assignments and task.assignments[0]["adapter"] == "mock"


def test_attention_queue_only_lists_person_tasks(tower):
    board = Board(tower)
    quiet = board.create("backend", "a")
    quiet.state = "waiting"
    board.save(quiet)
    needs = board.create("backend", "b")
    needs.state = "blocked"
    needs.attention = {"question": "staging or prod?"}
    board.save(needs)
    queue = [t.id for t in board.needs_person()]
    assert needs.id in queue
    assert quiet.id not in queue


def test_idempotency_key_prevents_duplicates(tower):
    board = Board(tower)
    first = board.create("backend", "one", idem_key="req-42")
    again = board.find_by_idem("req-42")
    assert again is not None and again.id == first.id
    assert board.find_by_idem("req-43") is None


def test_mandate_is_recorded(tower):
    board = Board(tower)
    task = board.create("backend", "x", mandate={"requester": "alice", "strength": "explicit"})
    assert board.load(task.id).mandate["requester"] == "alice"
