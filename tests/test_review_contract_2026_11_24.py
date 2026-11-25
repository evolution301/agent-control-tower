"""T-0045 A: the review contract and the parser share one findings heading.

A reviewer that copied the contract verbatim used to score zero findings, because
the contract annotated the heading (`## Findings — …`) while the parser keyed a
section by its whole heading. The origin was then accepted on a proved finding.
"""

import json
from pathlib import Path

import pytest

from control_tower import worker
from control_tower.board import Board
from control_tower.config import ensure_layout, parse_config
from control_tower.dispatch import FINDINGS_HEADING, parse_report
from control_tower.triage import triage_task

FINDING_BODY = "hero renders behind the fold | command: curl / → output: 200 with an empty body"
FINDING = f"- {FINDING_BODY}"


@pytest.fixture(autouse=True)
def no_detached_worker(monkeypatch):
    """`enqueue` would spawn a real detached worker that races this in-process run."""
    monkeypatch.setattr("control_tower.worker.ensure_worker", lambda cfg: False)


@pytest.fixture()
def tower(tmp_path, monkeypatch):
    config = {
        "name": "review-contract",
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


def _ready_task(tower):
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
    return task


def test_the_contract_heading_is_the_parser_key(tower, monkeypatch):
    board = Board(tower)
    task = _ready_task(tower)
    triage_task(tower, Board(tower), board.load(task.id))
    briefs: list[str] = []

    def fake_dispatch(cfg, board_, review_task, dry_run=False):
        briefs.append(review_task.brief)
        review_task.report = {"state": "ready", "summary": "clean",
                              "files": [], "checks": ["pytest → 3 passed"],
                              "self_review": "no findings — checks re-run"}
        board_.save(review_task)
        return review_task

    monkeypatch.setattr("control_tower.dispatch.dispatch", fake_dispatch)
    assert worker.run_worker(tower, once=True) == 0

    brief = briefs[0]
    assert f"\n{FINDINGS_HEADING}\n" in brief
    assert "## Findings —" not in brief, "the heading must be bare so a copy counts"

    # a reviewer that copies the contract's own heading line verbatim
    copied = f"{FINDINGS_HEADING}\n{FINDING}\n\n## Files\n- none\n"
    report = parse_report(copied)
    assert report["findings_section"] is True
    assert report["findings_fallback"] is False
    assert report["findings"] == [FINDING_BODY]


@pytest.mark.parametrize("heading", [
    "## Findings",
    "## Findings — one line per finding, or `- none` when clean:",
    "## Findings:",
    "## findings  –  annotated",
])
def test_annotated_headings_reach_the_same_section(heading):
    report = parse_report(f"{heading}\n{FINDING}\n")
    assert report["findings_section"] is True
    assert len(report["findings"]) == 1


def test_replay_of_the_qa_report_counts_its_finding():
    path = Path("roles/qa/T-0042-REPORT.md")
    if not path.exists():
        pytest.skip("roles/qa/T-0042-REPORT.md is not in this checkout")
    report = parse_report(path.read_text(encoding="utf-8"))
    assert len(report["findings"]) >= 1
