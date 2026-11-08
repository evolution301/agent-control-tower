"""Triage: the tower closes the ordinary loop itself (no human in the way)."""

import json
import sys

import pytest

from control_tower import cli
from control_tower.board import STATUS_ACCEPTED, STATUS_REPORTED, STATUS_REWORK, Board
from control_tower.config import ensure_layout, parse_config
from control_tower.triage import triage, triage_task


@pytest.fixture()
def tower(tmp_path, monkeypatch):
    config = {
        "name": "triage-tower",
        "workspace": str(tmp_path),
        "roles": {
            "backend": {"description": "server", "adapter": "mock",
                        "write_scopes": ["roles/backend/"], "max_rounds": 2},
            "qa": {"description": "verify", "adapter": "mock", "max_rounds": 0},
        },
        "verify_commands": [],
        "auto_triage": True,
    }
    path = tmp_path / "tower.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    cfg = parse_config(path)
    ensure_layout(cfg)
    monkeypatch.chdir(tmp_path)
    return cfg


def _good_report(task, root):
    (root / "roles" / task.role / "out.txt").write_text("x", encoding="utf-8")
    return {"state": "ready", "summary": "did it",
            "files": [f"roles/{task.role}/out.txt"], "checks": ["pytest → 3 passed"]}


def test_a_passing_report_is_accepted_without_a_human(tower, tmp_path):
    board = Board(tower)
    task = board.create("backend", "ship it")
    task.report = _good_report(task, tmp_path)
    board.save(task)
    board.transition(task, STATUS_REPORTED, note="report filed", report=task.report)

    result = triage_task(tower, board, board.load(task.id))
    assert result["outcome"] == "accepted"
    reloaded = board.load(task.id)
    assert reloaded.status == STATUS_ACCEPTED
    assert "auto_accepted" in tower.ledger_path.read_text(encoding="utf-8")


def test_a_failing_report_is_reworked_with_its_own_reasons(tower, tmp_path):
    board = Board(tower)
    task = board.create("backend", "ship it badly")
    task.report = {"state": "ready", "summary": "did it", "files": ["roles/backend/ghost.txt"],
                   "checks": ["nothing"]}
    board.save(task)
    board.transition(task, STATUS_REPORTED, note="report filed", report=task.report)

    sent: list[str] = []

    def fake_dispatch(_board, t, reasons):
        sent.append(reasons)

    result = triage_task(tower, board, board.load(task.id), dispatch_fn=fake_dispatch)
    assert result["outcome"] == "reworked"
    assert result["round"] == 1
    assert sent and "do not exist" in sent[0], "the role must be told what failed"
    assert board.load(task.id).status == STATUS_REWORK


def test_rounds_exhausted_escalates_to_a_person(tower, tmp_path):
    board = Board(tower)
    task = board.create("qa", "no budget for retries")   # max_rounds 0 on qa
    task.report = {"state": "ready", "summary": "s", "files": ["roles/qa/none.txt"],
                   "checks": ["x"]}
    board.save(task)
    board.transition(task, STATUS_REPORTED, note="report filed", report=task.report)

    result = triage_task(tower, board, board.load(task.id))
    assert result["outcome"] == "escalated"
    reloaded = board.load(task.id)
    assert reloaded.attention and reloaded.attention["question"]
    assert task.id in [t.id for t in board.needs_person()]


def test_restating_is_left_to_the_lead(tower):
    board = Board(tower)
    created = cli.main(["dispatch", "backend", "needs a restatement", "--restate"])
    assert created == 0
    task = board.load("T-0001")
    result = triage_task(tower, board, task)
    assert result["outcome"] == "awaiting_restatement_ok"


def test_cli_triage_reports_what_it_did(tower, tmp_path, capsys):
    board = Board(tower)
    task = board.create("backend", "will pass")
    task.report = _good_report(task, tmp_path)
    board.save(task)
    board.transition(task, STATUS_REPORTED, note="report filed", report=task.report)

    assert cli.main(["triage"]) == 0
    out = capsys.readouterr().out
    assert "triaged 1 item" in out
    assert board.load(task.id).status == STATUS_ACCEPTED


def test_cli_dispatch_auto_triages_by_default(tower, capsys):
    """A person should never have to accept a report the verifier already passed."""
    assert cli.main(["dispatch", "backend", "auto", "--due", "1h"]) == 0
    out = capsys.readouterr().out
    assert "status=accepted" in out
    task = Board(tower).load("T-0001")
    assert task.status == STATUS_ACCEPTED


def test_no_auto_leaves_the_report_for_a_human(tower, capsys):
    assert cli.main(["dispatch", "backend", "manual", "--no-auto"]) == 0
    task = Board(tower).load("T-0001")
    assert task.status == STATUS_REPORTED


def test_needs_person_never_counts_done_work(tower):
    board = Board(tower)
    done = board.create("backend", "already accepted")
    done.state = "ready"
    done.status = STATUS_ACCEPTED
    board.save(done)
    assert board.needs_person() == []
    assert [t.id for t in board.needs_lead()] == []


def test_triage_whole_queue_reports_the_person_remainder(tower, tmp_path):
    board = Board(tower)
    ok = board.create("backend", "passes")
    ok.report = _good_report(ok, tmp_path)
    board.save(ok)
    board.transition(ok, STATUS_REPORTED, note="report filed", report=ok.report)
    stuck = board.create("backend", "blocked on a decision")
    stuck.state = "blocked"
    stuck.attention = {"question": "staging or prod?"}
    board.save(stuck)

    result = triage(tower, board)
    assert result["outcomes"]["accepted"] == 1
    assert result["needs_person"] == [stuck.id]


# ---- regression: the 2026-11-08 dispatch crash (contract mismatch) ----

def _failed_task(tower, board):
    task = board.create("backend", "browser task that timed out")
    task.max_rounds = 2
    board.save(task)
    board.transition(task, "failed", note="adapter exit 4")
    return task


def test_dispatch_fn_contract_is_three_args(tower):
    """The documented contract: dispatch_fn(board, task, reasons) — no TypeError."""
    board = Board(tower)
    task = _failed_task(tower, board)
    seen = []

    def contract_callback(board, task, reasons):   # exactly the contract shape
        seen.append((task.id, reasons))

    result = triage_task(tower, board, board.load(task.id), dispatch_fn=contract_callback)
    assert result["outcome"] == "retried"
    assert seen and seen[0][0] == task.id and seen[0][1], "the callback must receive reasons"


def test_a_four_arg_callback_is_exactly_what_crashed(tower):
    """Passing _triage_dispatch directly (4-arg) is the original crash — pinned."""
    from control_tower import cli

    board = Board(tower)
    task = _failed_task(tower, board)
    with pytest.raises(TypeError, match="reasons"):
        triage_task(tower, board, board.load(task.id), dispatch_fn=cli._triage_dispatch)


def test_dispatch_with_a_failed_task_on_the_board_does_not_crash(tower, capsys):
    """John's repro: dispatch while a failed task sits on the board — must exit 0."""
    board = Board(tower)
    _failed_task(tower, board)

    rc = cli.main(["dispatch", "backend", "new work", "--due", "1h"])
    assert rc == 0, "the next dispatch must survive auto-triage"
    out = capsys.readouterr().out
    assert "Traceback" not in out

    # the failed task itself is retried by the queue pass, not by the new dispatch
    assert cli.main(["triage"]) == 0
    reloaded = board.load("T-0001")
    assert reloaded.round == 1, "the failed task must be retried once"
    assert "auto_retry" in tower.ledger_path.read_text(encoding="utf-8")
