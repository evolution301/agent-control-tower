"""Settlement-based acceptance metrics and effective review roles."""

import json

import pytest

from control_tower.analytics import build_report, render_text
from control_tower.board import STATUS_ACCEPTED, STATUS_FAILED, Board
from control_tower.config import effective_review_roles, ensure_layout, parse_config


def _tower(tmp_path, roles, review_roles):
    config = {
        "name": "metrics-tower",
        "workspace": str(tmp_path),
        "roles": {name: {"description": name, "adapter": "mock"} for name in roles},
        "review_roles": review_roles,
        "verify_commands": [],
    }
    path = tmp_path / "tower.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    cfg = parse_config(path)
    ensure_layout(cfg)
    return cfg


def _task(board, role, status, round_=0):
    task = board.create(role, f"{status} task")
    task.status = status
    task.round = round_
    board.save(task)


def test_acceptance_is_accepted_over_settled(tmp_path):
    cfg = _tower(tmp_path, ["core"], ["auditor"])
    board = Board(cfg)
    for _ in range(6):
        _task(board, "core", STATUS_ACCEPTED)
    for _ in range(2):
        _task(board, "core", STATUS_ACCEPTED, round_=1)
    for _ in range(2):
        _task(board, "core", STATUS_FAILED)

    report = build_report(cfg, board)

    assert report["totals"]["settled"] == 10
    assert report["rates"]["acceptance"] == pytest.approx(0.8)
    assert report["rates"]["verified"] == pytest.approx(0.8)
    assert report["rates"]["rework"] == pytest.approx(0.2)
    assert "8 accepted / 10 settled" in render_text(report)


def test_unknown_review_roles_are_recorded_missing(tmp_path):
    cfg = _tower(tmp_path, ["cli", "core"], ["auditor"])

    assert effective_review_roles(cfg) == []
    assert cfg.review_roles_missing == ["auditor"]


def test_existing_review_roles_survive(tmp_path):
    cfg = _tower(tmp_path, ["cli", "core", "qa"], ["qa"])

    assert effective_review_roles(cfg) == ["qa"]
    assert cfg.review_roles_missing == []

    cfg.review_roles = ["auditor", "qa"]
    assert effective_review_roles(cfg) == ["qa"]
    assert cfg.review_roles_missing == ["auditor"]
