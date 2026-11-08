"""P2 反审计（reviewer governance）+ P3 reflect 沉淀."""

import json

import pytest

from control_tower import cli, worker
from control_tower.board import STATUS_ACCEPTED, STATUS_REWORK, Board
from control_tower.config import ensure_layout, parse_config
from control_tower.relay import duration_seconds


@pytest.fixture()
def tower(tmp_path, monkeypatch):
    config = {
        "name": "gov-tower",
        "workspace": str(tmp_path),
        "roles": {
            "seo": {"description": "worker", "adapter": "mock", "max_rounds": 3},
            "auditor": {"description": "reviewer", "adapter": "mock", "max_rounds": 1},
        },
        "review_roles": ["auditor"],
        "review_budget": 2,
    }
    path = tmp_path / "tower.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    cfg = parse_config(path)
    ensure_layout(cfg)
    monkeypatch.chdir(tmp_path)
    return cfg


def _ready(tower, brief="ship it"):
    board = Board(tower)
    artifact = tower.workspace / "roles" / "seo" / "out.txt"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("deliverable", encoding="utf-8")
    task = board.create("seo", brief)
    task.report = {"state": "ready", "summary": "s",
                   "files": ["roles/seo/out.txt"],
                   "checks": ["`type out.txt` → deliverable"],
                   "self_review": "no findings — checks re-run"}
    board.save(task)
    board.transition(task, "reported", note="filed", report=task.report)
    return task


def test_a_finding_without_a_run_proof_is_dropped(tower, monkeypatch):
    task = _ready(tower)
    triage_task = __import__("control_tower.triage", fromlist=["triage_task"]).triage_task
    triage_task(tower, board := Board(tower), board.load(task.id))

    def fake_dispatch(cfg_, board_, review_task, dry_run=False):
        review_task.report = {
            "state": "ready", "summary": "found things", "files": [],
            "checks": ["none"], "self_review": "n/a",
            "findings": [
                "looks risky — no command, no output",          # dropped: no run
                "naming is off | command: lint → output: warn", # has a run, but style? keep simple: proved
            ],
        }
        board_.save(review_task)
        return review_task

    monkeypatch.setattr("control_tower.dispatch.dispatch", fake_dispatch)
    worker.run_worker(tower, once=True)
    origin = board.load(task.id)
    rep_path = tower.state_dir / "reviews" / "auditor.json"
    rep = json.loads(rep_path.read_text(encoding="utf-8"))
    assert rep["reviewed"] >= 1 and "dropped" in rep
    assert origin.review["dropped"] >= 1


def test_an_out_of_scope_finding_is_dropped(tower, monkeypatch):
    task = _ready(tower)
    triage_task = __import__("control_tower.triage", fromlist=["triage_task"]).triage_task
    triage_task(tower, Board(tower), Board(tower).load(task.id))

    def fake_dispatch(cfg_, board_, review_task, dry_run=False):
        review_task.report = {
            "state": "ready", "summary": "found", "files": [],
            "checks": ["x"], "self_review": "n/a",
            "findings": ["unrelated file broken | command: cat unrelated/other.md → output: text"],
        }
        board_.save(review_task)
        return review_task

    monkeypatch.setattr("control_tower.dispatch.dispatch", fake_dispatch)
    worker.run_worker(tower, once=True)
    origin = Board(tower).load(task.id)
    assert origin.review["out_of_scope"] == 1


def test_two_voided_findings_trigger_one_rerun_then_open_issue(tower, monkeypatch):
    task = _ready(tower)
    triage_task = __import__("control_tower.triage", fromlist=["triage_task"]).triage_task
    triage_task(tower, Board(tower), Board(tower).load(task.id))

    calls = {"n": 0}

    def fake_dispatch(cfg_, board_, review_task, dry_run=False):
        calls["n"] += 1
        review_task.report = {
            "state": "ready", "summary": "found", "files": [],
            "checks": ["x"], "self_review": "n/a",
            "findings": ["no repro", "also no repro"],   # both voided
        }
        board_.save(review_task)
        return review_task

    monkeypatch.setattr("control_tower.dispatch.dispatch", fake_dispatch)
    assert worker.run_one_job(tower) is not None      # first void → rerun queued
    assert worker.queue_depth(tower) == 1
    rerun = next((tower.queue_dir / "pending").glob("*.json"))
    payload = json.loads(rerun.read_text(encoding="utf-8"))["payload"]
    assert payload.get("retry") is True and "voided" in payload.get("failure", "")
    assert worker.run_one_job(tower) is not None      # second void → open issue, no third
    assert worker.queue_depth(tower) == 0
    open_issues = [e for e in Board(tower).inbox() if e.get("event") == "review_open_issue"]
    assert open_issues and "twice" in open_issues[0]["summary"]
    assert calls["n"] == 2


def test_reputation_is_stored_and_printed_by_status(tower, monkeypatch, capsys):
    task = _ready(tower)
    triage_task = __import__("control_tower.triage", fromlist=["triage_task"]).triage_task
    triage_task(tower, Board(tower), Board(tower).load(task.id))
    worker.run_worker(tower, once=True)
    rep = json.loads((tower.state_dir / "reviews" / "auditor.json").read_text(encoding="utf-8"))
    assert rep["reviewer"] == "auditor" and rep["reviewed"] >= 1 and "score" in rep

    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "auditor" in out and "score=" in out


def test_accept_after_two_reworks_drafts_a_lesson_skill(tower, monkeypatch, capsys):
    board = Board(tower)
    task = _ready(tower, "the checkout flow")
    # two rework rounds with named causes
    for note in ("missed the fold case", "forgot mobile layout"):
        board.transition(task, STATUS_REWORK, note=note)
    board.transition(task, "reported", note="re-filed after fixes",
                     report=task.report)
    triage_task = __import__("control_tower.triage", fromlist=["triage_task"]).triage_task
    triage_task(tower, board, board.load(task.id))    # verify ok → adversarial queued
    assert worker.run_worker(tower, once=True) == 0   # reviewer passes → origin accepted

    drafts = list((tower.workspace / "roles" / "seo" / "skills" / "drafts").glob("*.md"))
    assert drafts, "a structural lesson must be drafted"
    body = drafts[0].read_text(encoding="utf-8")
    assert "missed the fold case" in body
    assert "lesson_drafted" in tower.state_dir.joinpath("events.jsonl").read_text(encoding="utf-8")


def test_tower_reflect_manual_command(tower, monkeypatch, capsys):
    board = Board(tower)
    task = _ready(tower, "manual reflect")
    for note in ("one", "two"):
        board.transition(task, STATUS_REWORK, note=note)
    assert cli.main(["reflect", task.id]) == 0
    out = capsys.readouterr().out
    assert "draft skill written" in out
    capsys.readouterr()
    assert cli.main(["reflect", task.id]) == 1, "nothing new to distil twice"


def test_duration_seconds_reads_the_doc_grammar():
    assert duration_seconds("45m") == 2700
    assert duration_seconds("1h30m") == 300, "compound falls back to default"
