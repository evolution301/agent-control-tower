"""T-0045 B: an auto-queued review must settle both tasks and record its verdict.

Before this, the reviewer's own task was triaged inside the adversarial job: a
failed reviewer turn staged an auto-retry that blocked the job (so the verdict was
never recorded) and the review task was left in `rework` forever.
"""

import json

import pytest

from control_tower import worker
from control_tower.board import (STATUS_ACCEPTED, STATUS_FAILED, STATUS_REPORTED,
                                 STATUS_REWORK, Board)
from control_tower.config import ensure_layout, parse_config
from control_tower.triage import triage_task

FINDING = "hero renders behind the fold | command: curl / → output: 200 with an empty body"


@pytest.fixture(autouse=True)
def no_detached_worker(monkeypatch):
    """`enqueue` would spawn a real detached worker that races this in-process run."""
    monkeypatch.setattr("control_tower.worker.ensure_worker", lambda cfg: False)


@pytest.fixture()
def tower(tmp_path, monkeypatch):
    config = {
        "name": "review-settlement",
        "workspace": str(tmp_path),
        "roles": {
            "seo": {"description": "worker", "adapter": "mock", "max_rounds": 2},
            "auditor": {"description": "reviewer", "adapter": "mock", "max_rounds": 1},
        },
        "review_roles": ["auditor"],
        "review_budget": 1,
    }
    path = tmp_path / "tower.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    cfg = parse_config(path)
    ensure_layout(cfg)
    monkeypatch.chdir(tmp_path)
    return cfg


def _queued_review(tower):
    board = Board(tower)
    task = board.create("seo", "ship the hero")
    artifact = tower.workspace / "roles" / "seo" / "out.txt"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("deliverable", encoding="utf-8")
    task.report = {"state": "ready", "summary": "done",
                   "files": ["roles/seo/out.txt"],
                   "checks": ["`type out.txt` → deliverable"],
                   "self_review": "no findings — checks re-run"}
    board.save(task)
    board.transition(task, "reported", note="filed", report=task.report)
    triage_task(tower, Board(tower), board.load(task.id))
    return task


def _reviewer(tower, findings=(), failed=False):
    def fake_dispatch(cfg, board_, review_task, dry_run=False):
        if failed:
            board_.transition(review_task, STATUS_FAILED, note="adapter exit 4")
            return review_task
        review_task.report = {
            "state": "ready", "summary": "reviewed",
            "files": [], "checks": ["pytest -q → 21 passed"],
            "self_review": "no findings — checks re-run",
            "findings": list(findings),
        }
        board_.save(review_task)
        return review_task

    return fake_dispatch


def _events(board, name):
    return [e for e in board.inbox() if e.get("event") == name]


def test_a_pass_verdict_closes_the_review_task_as_accepted(tower, monkeypatch):
    task = _queued_review(tower)
    monkeypatch.setattr("control_tower.dispatch.dispatch", _reviewer(tower))
    assert worker.run_worker(tower, once=True) == 0

    board = Board(tower)
    origin = board.load(task.id)
    assert origin.status == STATUS_ACCEPTED
    assert origin.review["verdict"] == "pass"
    review = board.load(origin.review["review_task"])
    assert review.status == STATUS_ACCEPTED
    assert review.history[-1]["note"] == f"review closed: pass on {task.id}"
    assert _events(board, "review_verdict"), "the verdict push must fire on a pass"


def test_a_rework_verdict_closes_the_review_task(tower, monkeypatch):
    task = _queued_review(tower)
    monkeypatch.setattr("control_tower.dispatch.dispatch", _reviewer(tower, [FINDING]))
    assert worker.run_worker(tower, once=True) == 0

    board = Board(tower)
    origin = board.load(task.id)
    assert origin.status == STATUS_REWORK
    assert origin.review["verdict"] == "rework" and origin.review["proved"] == 1
    review = board.load(origin.review["review_task"])
    assert review.status == "closed"
    assert "review closed: rework" in review.history[-1]["note"]
    assert _events(board, "review_verdict"), "the verdict push must fire on a rework"


def test_a_reviewer_that_filed_nothing_never_accepts_the_origin(tower, monkeypatch):
    task = _queued_review(tower)
    monkeypatch.setattr("control_tower.dispatch.dispatch", _reviewer(tower, failed=True))
    assert worker.run_worker(tower, once=True) == 0

    board = Board(tower)
    origin = board.load(task.id)
    assert origin.status == STATUS_REPORTED, "silence is not an approval"
    assert origin.review["verdict"] == "unreviewed"
    review = board.load(origin.review["review_task"])
    assert review.status == STATUS_FAILED
    assert _events(board, "review_missing"), "the lead must hear that the review died"
    assert worker.queue_depth(tower) == 0, "no auto-retry may swallow the review job"
    assert "auto_retry" not in (tower.ledger_path.read_text(encoding="utf-8"))
