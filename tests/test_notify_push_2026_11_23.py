"""Settlements must reach the secretary, not just the pull-only tower inbox."""

import json
from types import SimpleNamespace

from control_tower import cli, worker
from control_tower.adapters import AdapterResult
from control_tower.board import Board
from control_tower.config import ensure_layout, parse_config


def _tower(tmp_path, roles, sessions=None, notify=None):
    config = {
        "name": "notify-tower",
        "workspace": str(tmp_path / "tower"),
        "roles": {name: {"description": name, "adapter": "mock"} for name in roles},
        "adapters": {"dsh": {"sessions": dict(sessions or {})}},
    }
    if notify:
        config["notify"] = notify
    path = tmp_path / "tower.toml"
    path.write_text(json.dumps(config), encoding="utf-8")
    cfg = parse_config(path)
    ensure_layout(cfg)
    return cfg


def _capture(monkeypatch):
    pushes = []
    from control_tower.adapters.mock import MockAdapter

    def deliver(self, session_id, text):
        pushes.append((session_id, text))
        return AdapterResult(text="pushed", exit_code=0)

    monkeypatch.setattr(MockAdapter, "deliver_to_session", deliver)
    return pushes


def test_settlement_notice_carries_report_counts_and_message(tmp_path):
    cfg = _tower(tmp_path, ["lead", "qa"])
    task = SimpleNamespace(
        id="T-0009",
        role="qa",
        status="ready",
        state="settled",
        report={
            "summary": "did the thing",
            "files": ["a.py", "b.py"],
            "checks": ["pytest -q → green"],
            "raw": "## Status\nready\n\n## Message to lead\nplease review T-0009\n",
        },
    )

    notice = worker._settlement_notice(cfg, task, {"outcome": "accepted"})

    assert "report: 2 files, 1 checks" in notice["summary"]
    assert "## Message to lead" in notice["summary"]
    assert "please review T-0009" in notice["summary"]


def test_inbox_tower_with_lead_pushes_to_its_session(tmp_path, monkeypatch):
    cfg = _tower(tmp_path, ["lead", "qa"], sessions={"lead": "session-lead-1"})
    pushes = _capture(monkeypatch)
    task = SimpleNamespace(id="T-0009", role="qa", report={})

    assert worker._deliver_lead_push(cfg, Board(cfg), task, "settled") is True
    assert pushes == [("session-lead-1", "settled")]


def test_doctor_warns_that_inbox_settlements_never_reach_the_secretary(
    tmp_path, capsys
):
    _tower(tmp_path, ["lead", "qa"])

    cli.main(["--workspace", str(tmp_path), "doctor"])

    out = capsys.readouterr().out
    assert "settlements only go to .tower/inbox — the secretary is never told" in out
    assert "set [notify] kind = lead_session and session = <your session id>" in out


def test_notify_test_without_target_prints_the_fallback(tmp_path, capsys):
    _tower(tmp_path, ["lead", "qa"])

    assert cli.main(["--workspace", str(tmp_path), "notify", "test"]) == 1
    assert "no target — set [notify] session" in capsys.readouterr().out


def test_notify_test_pushes_to_the_resolved_session(tmp_path, capsys, monkeypatch):
    _tower(tmp_path, ["lead", "qa"], sessions={"lead": "session-lead-2"})
    pushes = _capture(monkeypatch)

    assert cli.main(["--workspace", str(tmp_path), "notify", "test"]) == 0
    assert "pushed to session-lead-2 (exit 0)" in capsys.readouterr().out
    assert pushes == [("session-lead-2", "[tower] notify test")]
