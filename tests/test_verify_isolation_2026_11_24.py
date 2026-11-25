"""Verification must not call another lane's concurrent edit a defect.

T-0023 (0.18.2): accept ran the suite while two lanes were writing src/ and
packages/, the run failed, and auto-triage sent a passing task back to rework.
"""

import json
import shutil
import subprocess

import pytest

from control_tower import verify
from control_tower.board import Board, STATUS_REPORTED
from control_tower.config import ensure_layout, parse_config
from control_tower.triage import triage_task
from control_tower.worker import _settlement_notice

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git required")

GIT = ["git", "-c", "user.email=tower@example.invalid", "-c", "user.name=tower"]


def _git(repo, *args):
    subprocess.run([*GIT, "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


@pytest.fixture()
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "other.txt").write_text("base\n", encoding="utf-8")
    (root / "board.py").write_text("BASE = 1\n", encoding="utf-8")
    (root / "worker.py").write_text("from board import BASE\nVALUE = BASE\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "base")
    return root


def _cfg(tmp_path, repo, commands):
    path = tmp_path / "tower.json"
    path.write_text(json.dumps({
        "name": "verify-iso",
        "workspace": str(repo),
        "project_root": str(repo),
        "self_review": False,
        "roles": {"qa": {"description": "qa", "adapter": "mock"}},
        "accept_requires": ["summary", "files", "checks"],
        "verify_commands": commands,
    }), encoding="utf-8")
    cfg = parse_config(path)
    ensure_layout(cfg)
    return cfg


def _task(cfg):
    board = Board(cfg)
    task = board.create("qa", "verify isolation")
    task.report = {
        "state": "ready",
        "summary": "done",
        "files": ["app.py"],
        "checks": ["python -m pytest -q → see output"],
    }
    board.save(task)
    board.transition(task, STATUS_REPORTED, note="report filed")
    return board, task


def test_tree_change_during_run_is_deferred_not_rework(tmp_path, repo, monkeypatch):
    cfg = _cfg(tmp_path, repo, [
        "python -c \"import time; time.sleep(0.4); "
        "open('other.txt', 'a').write('x'); raise SystemExit(1)\"",
    ])
    # Another lane already edited a file this task does not claim; force the
    # quiet-window fallback instead of the worktree.
    (repo / "other.txt").write_text("other lane\n", encoding="utf-8")
    monkeypatch.setattr(verify, "_run_isolated", lambda *args, **kwargs: None)
    board, task = _task(cfg)

    verdict = verify.verify_report(cfg, task)
    assert not verdict.ok
    assert verdict.deferred
    assert any("verify-deferred" in p for p in verdict.problems)

    result = triage_task(cfg, board, task)

    assert result["outcome"] == "verify_deferred"
    assert task.status == "reported"
    assert (task.attention or {}).get("status") == "open"
    assert (task.attention or {}).get("question", "").startswith("verify-deferred:")
    assert "verify_deferred" in cfg.ledger_path.read_text(encoding="utf-8")

    from control_tower.worker import _settlement_notice

    notice = _settlement_notice(cfg, task, result)
    assert "triage: verify_deferred" in notice["summary"]
    shown = task.to_dict()
    assert shown["status"] == "reported"
    assert shown["attention"]["question"].startswith("verify-deferred:")


def test_verify_runs_against_the_uncommitted_working_set(tmp_path, repo):
    # The live tree carries another lane's edit; the isolated run must see that
    # same working set (what qa reviewed), not just HEAD.
    (repo / "other.txt").write_text("other lane\n", encoding="utf-8")
    cfg = _cfg(tmp_path, repo, [
        "python -c \"import pathlib, sys; "
        "sys.exit(0 if pathlib.Path('other.txt').read_text() == 'other lane\\n' else 1)\"",
    ])
    board, task = _task(cfg)

    verdict = verify.verify_report(cfg, task)

    assert verdict.ok, verdict.problems
    assert verdict.ran[0].get("isolated") is True


def test_genuine_failure_still_reworks(tmp_path, repo):
    cfg = _cfg(tmp_path, repo, ['python -c "raise SystemExit(1)"'])
    board, task = _task(cfg)

    verdict = verify.verify_report(cfg, task)
    assert not verdict.ok
    assert not verdict.deferred

    result = triage_task(cfg, board, task)

    assert result["outcome"] == "reworked"
    assert task.status == "rework"


def test_worktree_carries_sibling_uncommitted_edit(tmp_path, repo):
    # T-0030's worker.py depends on a constant added to board.py in the same
    # uncommitted working set; board.py is not in the task's file list.
    (repo / "board.py").write_text("BASE = 1\nSTATUS_LATE = 'late'\n", encoding="utf-8")
    (repo / "worker.py").write_text(
        "from board import BASE, STATUS_LATE\nVALUE = (BASE, STATUS_LATE)\n",
        encoding="utf-8")
    cfg = _cfg(tmp_path, repo, ['python -c "import worker"'])
    board, task = _task(cfg)
    task.report["files"] = ["worker.py"]
    board.save(task)

    verdict = verify.verify_report(cfg, task)

    assert verdict.ok, verdict.problems
    assert verdict.ran[0].get("isolated") is True


def test_broken_own_file_still_rejected(tmp_path, repo):
    (repo / "other.txt").write_text("other lane\n", encoding="utf-8")
    (repo / "app.py").write_text("raise SystemExit(1)\n", encoding="utf-8")
    cfg = _cfg(tmp_path, repo, ['python -c "import app"'])
    board, task = _task(cfg)

    verdict = verify.verify_report(cfg, task)

    assert not verdict.ok
    assert not verdict.deferred
    result = triage_task(cfg, board, task)
    assert result["outcome"] == "reworked"
    assert task.status == "rework"
