"""`tower accept` must agree with verify_gate.py about what a `## Files` line is.

The aimigao-zh incident (T-0019, 2026-11-24): prose notes in `## Files` were
stat'ed as paths, the report was rejected, and a correct deliverable went back
to rework while the role's own gate said PASS.
"""

import json

from control_tower.board import Board
from control_tower.config import ensure_layout, parse_config
from control_tower.verify import split_file_candidates, verify_report


def _tower(tmp_path):
    path = tmp_path / "tower.json"
    path.write_text(json.dumps({
        "name": "accept-files",
        "workspace": str(tmp_path),
        "roles": {"seo": {"description": "s", "adapter": "mock"}},
        "accept_requires": ["summary", "files", "checks"],
    }), encoding="utf-8")
    cfg = parse_config(path)
    ensure_layout(cfg)
    return cfg


def _task(cfg, files):
    task = Board(cfg).create("seo", "files gate")
    task.report = {
        "state": "ready",
        "summary": "done",
        "files": files,
        "checks": ["python -m pytest -q → 364 passed"],
        "self_review": "no findings — checks re-run",
    }
    return task


def test_prose_between_real_paths_does_not_reject(tmp_path):
    cfg = _tower(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("x", encoding="utf-8")
    shots = tmp_path / "shots"
    shots.mkdir()
    for name in ("home-1440.png", "home-390.png", "faq-1440.png", "faq-390.png"):
        (shots / name).write_text("x", encoding="utf-8")

    files = [
        "两份交付都在这两个路径",
        "docs/a.md",
        "无新增文件（仅修改）",
        str(tmp_path / "docs" / "a.md"),
        "shots/{home,faq}-{1440,390}.png",
    ]
    task = _task(cfg, files)

    assert split_file_candidates("两份交付都在这两个路径") == []
    assert split_file_candidates("无新增文件（仅修改）") == []
    assert split_file_candidates("docs/a.md") == ["docs/a.md"]

    v = verify_report(cfg, task)
    assert not any("do not exist" in p for p in v.problems), v.problems
    assert v.ok


def test_prose_only_files_is_no_files_claimed(tmp_path):
    cfg = _tower(tmp_path)
    task = _task(cfg, ["两份交付都在这两个路径", "无新增文件（仅修改）"])

    v = verify_report(cfg, task)

    assert not any("do not exist" in p for p in v.problems), v.problems
    assert v.ok, "prose-only Files is a note, not a claim about missing files"


def test_missing_path_still_fails_with_the_same_message(tmp_path):
    cfg = _tower(tmp_path)
    task = _task(cfg, ["两份交付都在这两个路径", "docs/missing.md"])

    v = verify_report(cfg, task)

    assert not v.ok
    [problem] = [p for p in v.problems if "do not exist" in p]
    assert problem.startswith("reported files do not exist (checked: ")
    assert problem.endswith(": docs/missing.md")
