"""B11–B16 field batch (prl-ops, 2026-11-09 evening)."""

import json
import pathlib
import subprocess
import sys

import pytest

from control_tower import cli
from control_tower.board import Board
from control_tower.config import ensure_layout, parse_config
from control_tower.dispatch import parse_report
from control_tower.snapshot import build_snapshot
from control_tower.verify import split_file_candidates, verify_report


def _tower(tmp_path, monkeypatch, **overrides):
    config = {
        "name": "b11-16",
        "workspace": str(tmp_path),
        "roles": {"seo": {"description": "s", "adapter": "mock", "max_rounds": 1}},
        "accept_requires": ["summary", "files", "checks"],
    }
    config.update(overrides)
    path = tmp_path / "tower.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    cfg = parse_config(path)
    ensure_layout(cfg)
    monkeypatch.chdir(tmp_path)
    return cfg


def _report(**kw):
    report = {"state": "ready", "summary": "done",
              "files": ["out.md"], "checks": ["pytest → 1 passed"],
              "self_review": "no findings — checks re-run"}
    report.update(kw)
    return report


def _age_task_file(cfg, task_id, hours):
    from datetime import datetime, timedelta

    path = cfg.tasks_dir / f"{task_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    stamp = (datetime.now().astimezone() - timedelta(hours=hours))
    data["updated_at"] = stamp.strftime("%Y-%m-%dT%H:%M:%S%z")
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_b11_stale_queue_task_leaves_backlog_and_gets_a_count(tmp_path, monkeypatch):
    cfg = _tower(tmp_path, monkeypatch)
    (tmp_path / "out.md").write_text("x", encoding="utf-8")
    board = Board(cfg)
    task = board.create("seo", "old receipt")
    task.report = _report()
    task.state = "ready"
    board.transition(task, "reported", note="old")
    _age_task_file(cfg, task.id, hours=30)

    assert board.stale_tasks() and board.stale_tasks()[0].id == task.id
    assert board.needs_lead() == [], "陈旧不算「待总控」"
    snap = build_snapshot(cfg)
    assert snap.counts["stale"] == 1
    assert snap.triage == []
    assert [item["id"] for item in snap.stale] == [task.id]


def test_b11_config_stale_hours(tmp_path, monkeypatch):
    cfg = _tower(tmp_path, monkeypatch, stale_hours=1)
    board = Board(cfg)
    task = board.create("seo", "two hours old")
    task.report = _report()
    board.transition(task, "reported", note="old")
    _age_task_file(cfg, task.id, hours=2)
    assert board.is_stale(board.load(task.id))


def test_b11_terminal_dropped_tasks_belong_to_no_queue(tmp_path, monkeypatch):
    cfg = _tower(tmp_path, monkeypatch)
    board = Board(cfg)
    task = board.create("seo", "hand-dropped old task")
    board.transition(task, "dropped", note="closed by hand")
    _age_task_file(cfg, task.id, hours=48)
    assert not board.is_stale(board.load(task.id)), "terminal is not 陈旧"
    assert board.needs_lead() == [] and board.needs_person() == []
    assert build_snapshot(cfg).counts["stale"] == 0


def test_b12_triage_retry_is_opt_in(tmp_path, monkeypatch):
    cfg = _tower(tmp_path, monkeypatch)
    captured = {}

    def fake_triage(cfg_, board_, dispatch_fn=None):
        captured["fn"] = dispatch_fn
        return {"touched": 0, "outcomes": {}, "results": [], "needs_person": []}

    monkeypatch.setattr(cli, "run_triage", fake_triage)
    assert cli.main(["triage"]) == 0
    assert captured["fn"] is None, "默认只 verify/accept，不重派"
    assert cli.main(["triage", "--retry", "--budget", "5"]) == 0
    assert callable(captured["fn"]), "--retry 才重派"


def test_b13_status_json_is_pure_ascii(tmp_path, monkeypatch):
    cfg = _tower(tmp_path, monkeypatch)
    board = Board(cfg)
    board.create("seo", "中文简报 C:\\Users\\Administrator\\x")
    proc = subprocess.run(
        [sys.executable, "-m", "control_tower", "--workspace", str(tmp_path),
         "status", "--json"],
        capture_output=True, timeout=60,
    )
    assert proc.returncode == 0
    raw = proc.stdout
    assert raw, "status --json must print"
    assert max(raw) < 128, "B13: --json must be pure ASCII for any Windows pipe"
    data = json.loads(raw.decode("ascii"))
    assert isinstance(data, list) and data


def test_b14_multi_path_line_is_split(tmp_path, monkeypatch):
    cfg = _tower(tmp_path, monkeypatch)
    for name in ("a.json", "b.json", "c.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    task = Board(cfg).create("seo", "multi")
    task.report = _report(files=["a.json、b.json、c.json"])
    v = verify_report(cfg, task)
    assert not any("do not exist" in p for p in v.problems)
    assert split_file_candidates("a.json、b.json") == ["a.json", "b.json"]

    task.report = _report(files=["a.json、missing.json"])
    v2 = verify_report(cfg, task)
    assert any("do not exist" in p and "missing.json" in p and "a.json" not in p
               for p in v2.problems)


def test_b15_accept_uses_the_fresh_markdown_report(tmp_path, monkeypatch):
    cfg = _tower(tmp_path, monkeypatch)
    (tmp_path / "out.md").write_text("x", encoding="utf-8")
    board = Board(cfg)
    task = board.create("seo", "stale cache")
    task.report = _report(files=["gone.md"])       # cached copy is wrong
    board.transition(task, "reported", note="filed")

    report_dir = cfg.role_dir("seo")
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / f"{task.id}-REPORT.md").write_text(
        "## Status: ready\n\n## Summary\ndone\n\n## Files\n- out.md\n\n"
        "## Checks\n- pytest → 1 passed\n\n## Self-review\nno findings — checks re-run\n",
        encoding="utf-8")
    task = board.load(task.id)
    v = verify_report(cfg, task)
    assert not any("do not exist" in p for p in v.problems), v.problems


def test_b16_legacy_self_review(tmp_path, monkeypatch):
    cfg = _tower(tmp_path, monkeypatch, self_review_since="2026-11-10T00:00:00+0800")
    (tmp_path / "out.md").write_text("x", encoding="utf-8")
    task = Board(cfg).create("seo", "old report")
    task.created_at = "2026-11-08T10:00:00+0800"
    task.report = _report(self_review="")
    assert not any("Self-review" in p for p in verify_report(cfg, task).problems), \
        "reports predating the gate are compatible"
    task.created_at = "2026-11-11T10:00:00+0800"
    assert any("Self-review" in p for p in verify_report(cfg, task).problems)
    # --legacy overrides regardless of dates
    assert not any("Self-review" in p
                   for p in verify_report(cfg, task, legacy=True).problems)
