"""`tower accept` must not accept a claim nobody reviewed."""

import json

from control_tower import cli
from control_tower.board import STATUS_ACCEPTED, STATUS_REPORTED, Board
from control_tower.config import ensure_layout, parse_config


def _tower(tmp_path, review_roles):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "app.py").write_text("print('ok')\n", encoding="utf-8")
    config = {
        "name": "review-gate",
        "workspace": str(workspace),
        "roles": {
            "core": {"description": "core", "adapter": "mock"},
            "qa": {"description": "qa", "adapter": "mock"},
        },
        "review_roles": review_roles,
        "verify_commands": [],
    }
    path = tmp_path / "tower.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    cfg = parse_config(path)
    ensure_layout(cfg)
    return cfg, path


def _reported(cfg):
    board = Board(cfg)
    task = board.create("core", "a deliverable")
    task.status = STATUS_REPORTED
    task.report = {
        "state": "ready",
        "summary": "did the thing",
        "files": ["app.py"],
        "checks": ["python -m pytest -q → 1 passed"],
        "self_review": "no findings — checks re-run",
    }
    board.save(task)
    return board, task


def _accept(path, task_id, *extra):
    return cli.main(["--workspace", str(path), "accept", task_id, *extra])


def test_no_verdict_with_qa_present_is_refused(tmp_path, capsys):
    cfg, path = _tower(tmp_path, ["qa"])
    board, task = _reported(cfg)

    assert _accept(path, task.id) == 2
    out = capsys.readouterr().out
    assert f"task {task.id} has no qa verdict" in out
    assert f"`tower review {task.id}`" in out
    assert "--no-review" in out
    assert board.load(task.id).status == STATUS_REPORTED


def test_verdict_present_is_accepted(tmp_path):
    cfg, path = _tower(tmp_path, ["qa"])
    board, task = _reported(cfg)
    task.review = {"verdict": "pass", "reviewer": "qa", "rounds": 1}
    board.save(task)

    assert _accept(path, task.id) == 0
    assert board.load(task.id).status == STATUS_ACCEPTED


def test_no_review_flag_accepts_and_records_waiver(tmp_path):
    cfg, path = _tower(tmp_path, ["qa"])
    board, task = _reported(cfg)

    assert _accept(path, task.id, "--no-review") == 0
    saved = board.load(task.id)
    assert saved.status == STATUS_ACCEPTED
    assert saved.review["waived_by"] == "human"


def test_no_effective_review_roles_keeps_old_behaviour(tmp_path):
    cfg, path = _tower(tmp_path, [])
    board, task = _reported(cfg)

    assert _accept(path, task.id) == 0
    assert board.load(task.id).status == STATUS_ACCEPTED
