"""The adversarial review loop: 自审 + 对抗审查 + 反审计的前半段."""

import json

import pytest

from control_tower import cli, worker
from control_tower.board import STATUS_ACCEPTED, STATUS_REWORK, Board
from control_tower.config import ensure_layout, parse_config
from control_tower.triage import triage_task
from control_tower.verify import verify_report


@pytest.fixture()
def tower(tmp_path, monkeypatch):
    config = {
        "name": "review-tower",
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


def _ready_task(tower, brief="ship the hero"):
    board = Board(tower)
    task = board.create("seo", brief)
    artifact = tower.workspace / "roles" / "seo" / "out.txt"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("deliverable", encoding="utf-8")
    task.report = {"state": "ready", "summary": "done",
                   "files": ["roles/seo/out.txt"],
                   "checks": ["`type out.txt` → deliverable"],
                   "self_review": "no findings — checks re-run"}
    board.save(task)
    board.transition(task, "reported", note="filed", report=task.report)
    return task


def test_verify_gate_requires_a_substantive_self_review(tower):
    board = Board(tower)
    task = board.create("seo", "no self-review")
    task.report = {"state": "ready", "summary": "s", "files": [],
                   "checks": ["pytest → 3 passed"]}
    board.save(task)
    problems = verify_report(tower, task).problems
    assert any("Self-review" in p for p in problems)
    task.report["self_review"] = "no findings — checks re-run"
    assert not any("Self-review" in p for p in verify_report(tower, task).problems)


def test_verify_pass_queues_an_adversarial_review(tower):
    task = _ready_task(tower)
    result = triage_task(tower, Board(tower), Board(tower).load(task.id))
    assert result["outcome"] == "review_queued"
    reloaded = Board(tower).load(task.id)
    assert reloaded.review["verdict"] == "pending"
    assert reloaded.review["reviewer"] == "auditor"
    assert worker.queue_depth(tower) == 1
    assert "review_queued" in tower.ledger_path.read_text(encoding="utf-8")


def test_a_pending_review_blocks_other_triage(tower):
    task = _ready_task(tower)
    triage_task(tower, Board(tower), Board(tower).load(task.id))
    result = triage_task(tower, Board(tower), Board(tower).load(task.id))
    assert result["outcome"] == "awaiting_review"
    assert Board(tower).load(task.id).status != STATUS_ACCEPTED


def test_no_proved_findings_accepts_the_origin(tower):
    task = _ready_task(tower)
    triage_task(tower, Board(tower), Board(tower).load(task.id))
    assert worker.run_worker(tower, once=True) == 0

    board = Board(tower)
    assert board.load(task.id).status == STATUS_ACCEPTED
    review = board.load(task.id).review
    assert review["verdict"] == "pass" and review["proved"] == 0
    assert review["review_task"], "the reviewer's own task is linked"
    verdicts = [e for e in board.inbox() if e.get("event") == "review_verdict"]
    assert verdicts and verdicts[0]["verdict"] == "pass"
    assert "adversarial_verdict" in (tower.state_dir / "events.jsonl").read_text(encoding="utf-8")


def test_a_proved_finding_reworks_the_origin_with_its_proof(tower, monkeypatch):
    board = Board(tower)
    task = _ready_task(tower, brief="ship the hero")
    triage_task(tower, Board(tower), board.load(task.id))

    # the reviewer's turn comes back with one proved finding
    def fake_dispatch(cfg, board_, review_task, dry_run=False):
        review_task.report = {
            "state": "ready", "summary": "broke it",
            "files": [], "checks": ["repro → failed"],
            "self_review": "n/a for a review",
            "findings": ["hero renders behind the fold | command: curl / → output: 200 but empty body"],
        }
        board_.save(review_task)
        return review_task

    monkeypatch.setattr("control_tower.dispatch.dispatch", fake_dispatch)
    assert worker.run_worker(tower, once=True) == 0
    origin = board.load(task.id)
    assert origin.status != STATUS_ACCEPTED, "a proved finding must block acceptance"
    assert "curl /" in origin.brief, "the proof travels with the rework"
    assert origin.review["verdict"] == "rework" and origin.review["proved"] == 1
    verdicts = [e for e in board.inbox() if e.get("event") == "review_verdict"]
    assert verdicts and verdicts[0]["verdict"] == "rework"


def test_review_budget_zero_skips_the_loop(tower):
    tower.review_budget = 0
    task = _ready_task(tower)
    result = triage_task(tower, Board(tower), Board(tower).load(task.id))
    assert result["outcome"] == "accepted"
    assert worker.queue_depth(tower) == 0


def test_cli_review_triggers_a_manual_pass(tower):
    task = _ready_task(tower)
    assert cli.main(["review", task.id]) == 0
    assert worker.queue_depth(tower) == 1


def test_the_reviewer_never_reviews_itself(tower):
    board = Board(tower)
    artifact = tower.workspace / "roles" / "auditor" / "out.txt"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("deliverable", encoding="utf-8")
    task = board.create("auditor", "auditor's own work")
    task.report = {"state": "ready", "summary": "s",
                   "files": ["roles/auditor/out.txt"],
                   "checks": ["x → ok"], "self_review": "no findings"}
    board.save(task)
    board.transition(task, "reported", note="filed", report=task.report)
    result = triage_task(tower, board, board.load(task.id))
    assert result["outcome"] == "accepted", "the reviewer role cannot be its own reviewer"
