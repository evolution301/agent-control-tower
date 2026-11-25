"""The verdict must read the findings a reviewer actually wrote (T-0040).

qa filed proved findings in `## Summary` and the parser — which only counted
`## Findings` — returned 0, so the verdict said "pass, findings=0" over a
medium hole. These tests pin both halves: the section map the contract asks
for, and the fallback that counts ` | command: ` bullets when the section is
missing.
"""

import json
from pathlib import Path

import pytest

from control_tower import worker
from control_tower.board import STATUS_ACCEPTED, Board
from control_tower.config import ensure_layout, parse_config
from control_tower.dispatch import parse_report
from control_tower.triage import triage_task

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture()
def tower(tmp_path, monkeypatch):
    config = {
        "name": "verdict-tower",
        "workspace": str(tmp_path),
        "roles": {
            "seo": {"description": "worker", "adapter": "mock", "max_rounds": 2},
            "qa": {"description": "reviewer", "adapter": "mock", "max_rounds": 1},
        },
        "review_roles": ["qa"],
        "review_budget": 1,
    }
    path = tmp_path / "tower.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    cfg = parse_config(path)
    ensure_layout(cfg)
    monkeypatch.chdir(tmp_path)
    return cfg


def _ready_task(cfg, brief="ship the hero"):
    board = Board(cfg)
    task = board.create("seo", brief)
    artifact = cfg.workspace / "roles" / "seo" / "out.txt"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("deliverable", encoding="utf-8")
    task.report = {"state": "ready", "summary": "done",
                   "files": ["roles/seo/out.txt"],
                   "checks": ["`type out.txt` → deliverable"],
                   "self_review": "no findings — checks re-run"}
    board.save(task)
    board.transition(task, "reported", note="filed", report=task.report)
    return task


def _review(text):
    """A reviewer turn that answers with `text`, as the adapter would."""

    def fake_dispatch(cfg_, board_, review_task, dry_run=False):
        review_task.report = parse_report(text)
        board_.save(review_task)
        return review_task

    return fake_dispatch


def test_findings_section_lines_are_counted():
    text = (
        "## Status\nready\n\n"
        "## Findings\n"
        "- guard accepts 127.evil | command: `probe.py` → output: 200 dismissed\n"
        "- docs claim serial | command: `probe2.py` → output: peak 2\n\n"
        "## Files\n- a.py — x\n\n"
        "## Checks\n- pytest → 2 passed\n\n"
        "## Self-review\nno findings — checks re-run\n"
    )
    parsed = parse_report(text)
    assert parsed["findings_section"] is True
    assert parsed["findings_fallback"] is False
    assert len(parsed["findings"]) == 2
    assert all("command:" in f for f in parsed["findings"])


def test_findings_hidden_in_summary_are_counted_via_fallback():
    text = (
        "## Status\nready\n\n"
        "## Summary\n"
        "Reviewed the change; three defects remain, all proved by a run:\n"
        "- [medium] `_loopback` accepts `127.0.0.1.evil.com` | command: "
        "`python probe_t0033_origin.py` → output: `127.0.0.1.evil.com no SFS -> 200`\n"
        "- drift: docs say one turn per role | command: `python probe_serial.py` "
        "→ output: `peak same-role turns: 2`\n"
        "Everything else held.\n\n"
        "## Open issues\n"
        "- a follow-up with no command marker\n\n"
        "## Self-review\nno findings — checks re-run\n"
    )
    parsed = parse_report(text)
    assert parsed["findings_section"] is False
    assert parsed["findings_fallback"] is True
    assert len(parsed["findings"]) == 2
    assert any("127.0.0.1.evil.com" in f for f in parsed["findings"])


def test_clean_report_with_none_is_zero_findings(tower, monkeypatch):
    text = ("## Status\nready\n\n## Findings\n- none\n\n## Files\n- a.py — x\n\n"
            "## Checks\n- pytest → 3 passed\n\n## Self-review\nno findings — checks re-run\n")
    parsed = parse_report(text)
    assert parsed["findings"] == []
    assert parsed["findings_section"] is True
    assert parsed["findings_fallback"] is False

    board = Board(tower)
    task = _ready_task(tower)
    triage_task(tower, board, board.load(task.id))
    monkeypatch.setattr("control_tower.dispatch.dispatch", _review(text))
    assert worker.run_worker(tower, once=True) == 0
    origin = Board(tower).load(task.id)
    assert origin.status == STATUS_ACCEPTED and origin.review["findings"] == 0


def test_unproved_finding_shows_in_the_verdict_without_blocking(tower, monkeypatch):
    board = Board(tower)
    task = _ready_task(tower)
    triage_task(tower, board, board.load(task.id))

    report = (
        "## Status\nready\n\n"
        "## Findings\n"
        "- looks fragile | command: `pytest -q`\n\n"
        "## Files\n- a.py — x\n\n"
        "## Checks\n- pytest → 3 passed\n\n"
        "## Self-review\nno findings — checks re-run\n"
    )
    monkeypatch.setattr("control_tower.dispatch.dispatch", _review(report))
    assert worker.run_worker(tower, once=True) == 0

    origin = Board(tower).load(task.id)
    assert origin.status == STATUS_ACCEPTED, "an unproved finding must not block"
    assert origin.review["findings"] == 1
    assert origin.review["unproved"] == 1
    assert origin.review["proved"] == 0
    verdicts = [e for e in Board(tower).inbox() if e.get("event") == "review_verdict"]
    assert "unproved=1" in verdicts[0]["summary"]
    log = (tower.state_dir / "ledger.jsonl").read_text(encoding="utf-8")
    assert "\"unproved\": 1" in log
    brief = Board(tower).load(origin.review["review_task"]).brief
    assert "## Findings" in brief and "- none" in brief


def test_proved_finding_in_summary_reworks_the_origin(tower, monkeypatch):
    """The T-0033 shape, end to end: hidden finding -> rework, not accept."""
    board = Board(tower)
    task = _ready_task(tower)
    triage_task(tower, board, board.load(task.id))

    report = (
        "## Status\nready\n\n"
        "## Summary\n"
        "Three defects remain, all proved by a run:\n"
        "- loopback hole | command: `curl -s -o /dev/null -w %{http_code} "
        "http://127.0.0.1:9000/x` → output: `200 dismissed`\n\n"
        "## Self-review\nno findings — checks re-run\n"
    )
    monkeypatch.setattr("control_tower.dispatch.dispatch", _review(report))
    assert worker.run_worker(tower, once=True) == 0

    origin = Board(tower).load(task.id)
    assert origin.status != STATUS_ACCEPTED
    assert origin.review["verdict"] == "rework"
    assert origin.review["findings_fallback"] is True
    assert "loopback hole" in origin.brief


@pytest.mark.skipif(
    not (REPO / "roles" / "qa" / "T-0033-REPORT.md").exists(),
    reason="the archived qa report is not in this checkout",
)
def test_replaying_the_qa_report_finds_the_loopback_hole():
    text = (REPO / "roles" / "qa" / "T-0033-REPORT.md").read_text(encoding="utf-8")
    parsed = parse_report(text)
    assert parsed["findings_section"] is False
    assert parsed["findings_fallback"] is True
    assert len(parsed["findings"]) >= 1
    assert any("127.0.0.1.evil.com" in f for f in parsed["findings"])
