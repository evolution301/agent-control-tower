"""Field batch 2026-11-22: B1 pointer/binding, B3 orphans+doctor, B4 preflight, B5 init, onboard sessions."""

import json
import pathlib

import pytest

from control_tower import cli, worker
from control_tower.board import Board
from control_tower.config import ensure_layout, parse_config
from control_tower.onboard import onboard, parse_notes


def _tower(tmp_path, monkeypatch, **extra):
    tmp_path.mkdir(parents=True, exist_ok=True)
    config = {
        "name": "field-tower",
        "workspace": str(tmp_path),
        "roles": {"seo": {"description": "s", "adapter": "mock", "max_rounds": 1}},
    }
    config.update(extra)
    path = tmp_path / "tower.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    cfg = parse_config(path)
    ensure_layout(cfg)
    monkeypatch.chdir(tmp_path)
    return cfg


def test_b1_projects_use_rewrites_the_binding(tmp_path, monkeypatch):
    from control_tower import registry

    mine = tmp_path / "mine"
    _tower(mine, monkeypatch)
    other = tmp_path / "other"
    _tower(other, monkeypatch)
    registry.add_project(mine, name="mine")
    registry.add_project(other, name="other")
    assert cli.main(["projects", "use", "mine"]) == 0
    binding = registry.read_binding() or {}
    assert str(binding.get("path", "")).endswith("mine"), \
        "an explicit switch re-aims the binding so a mount record can't outrank it"


def test_b1_unknown_role_error_names_the_workspace(tmp_path, monkeypatch, capsys):
    cfg = _tower(tmp_path, monkeypatch)
    code = cli.main(["dispatch", "nope", "hi"])
    out = capsys.readouterr()
    assert code == 2
    assert "workspace" in (out.out + out.err)


def test_b3_worker_adopts_orphans_on_start(tmp_path, monkeypatch):
    cfg = _tower(tmp_path, monkeypatch)
    board = Board(cfg)
    task = board.create("seo", "died mid-run")
    board.transition(task, "in_progress", note="claimed")
    # age it past staleness and leave no queue claim
    path = cfg.tasks_dir / f"{task.id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["updated_at"] = "2026-11-01T00:00:00+0800"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    adopted = worker._adopt_orphans(cfg)
    assert adopted == 1
    reloaded = board.load(task.id)
    assert reloaded.status == "rework"
    events = (cfg.state_dir / "events.jsonl").read_text(encoding="utf-8")
    assert "task_interrupted" in events


def test_b3_live_queue_claims_are_not_adopted(tmp_path, monkeypatch):
    cfg = _tower(tmp_path, monkeypatch)
    board = Board(cfg)
    task = board.create("seo", "someone owns me")
    board.transition(task, "in_progress", note="claimed")
    path = cfg.tasks_dir / f"{task.id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["updated_at"] = "2026-11-01T00:00:00+0800"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    claimed = cfg.queue_dir / "claimed"
    claimed.mkdir(parents=True, exist_ok=True)
    (claimed / "Q-00001.json").write_text(
        json.dumps({"id": "Q-00001", "kind": "dispatch", "task": task.id}), encoding="utf-8")
    assert worker._adopt_orphans(cfg) == 0


def test_b4_dispatch_preflight_fails_fast_on_dead_base_url(tmp_path, monkeypatch, capsys):
    cfg = _tower(tmp_path, monkeypatch, roles={
        "seo": {"description": "s", "adapter": "dsh", "max_rounds": 1}},
        adapters={"dsh": {"base_url": "http://127.0.0.1:1", "secret": "x"}})
    code = cli.main(["dispatch", "seo", "hello"])
    out = capsys.readouterr()
    assert code == 2, "a stale base_url must refuse the dispatch, not hang it"
    assert "REFUSED (pre-flight)" in (out.out + out.err)
    assert "base_url" in (out.out + out.err)


def test_b4_preflight_skips_non_dsh(tmp_path, monkeypatch):
    cfg = _tower(tmp_path, monkeypatch)
    assert cli._dispatch_preflight(cfg, "seo") is None


def test_b5_init_template_has_no_self_grading_default(tmp_path):
    for name in ("tower.toml",):
        text = (tmp_path / name)
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        assert cli.main(["init", td]) == 0
        content = (pathlib.Path(td) / "tower.toml").read_text(encoding="utf-8")
        assert '- "echo ok"' not in content, "init must not ship a self-grading check"
        assert "verify_commands" in content, "the option is still documented (commented)"


def test_onboard_wires_session_and_title(tmp_path, monkeypatch):
    root = tmp_path / "t"
    root.mkdir()
    (root / "tower.toml").write_text("name: t\nworkspace: .\nroles: {}\n", encoding="utf-8")
    notes = """# What I do
site studio

## seo
keywords and TDK
scope: seo/
session: session-abc-123
title: My SEO Bot
"""
    monkeypatch.chdir(root)
    result = onboard(root / "tower.toml", notes=notes)
    assert result["roles"] == ["seo"]
    cfg = parse_config(root / "tower.toml")
    sessions = (cfg.adapter_options.get("dsh") or {}).get("sessions") or {}
    assert sessions.get("seo") == "session-abc-123"
    charter = root / "roles" / "seo" / "AGENT.md"
    assert charter.exists() and "My SEO Bot" in charter.read_text(encoding="utf-8")
    assert "four bindings" in result["next"].lower() or "FOUR bindings" in result["next"]
