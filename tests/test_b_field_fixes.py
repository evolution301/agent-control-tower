"""B1/B3/B9/B10 from the prl-ops field report (2026-11-09)."""

import json
import pathlib

import pytest

from control_tower import cli, worker
from control_tower.board import Board
from control_tower.config import ensure_layout, parse_config


@pytest.fixture()
def tower(tmp_path, monkeypatch):
    config = {
        "name": "b-fixes",
        "workspace": str(tmp_path),
        "roles": {"seo": {"description": "s", "adapter": "mock"}},
        "staleness_minutes": 30,
    }
    path = tmp_path / "tower.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    cfg = parse_config(path)
    ensure_layout(cfg)
    monkeypatch.chdir(tmp_path)
    return cfg


def test_b3_unstick_requeues_a_stuck_task(tower, capsys):
    board = Board(tower)
    task = board.create("seo", "stuck job")
    board.transition(task, "claimed", note="worker picked it up")

    assert cli.main(["unstick", task.id]) == 0
    reloaded = board.load(task.id)
    assert reloaded.status == "pending"
    assert "unstuck" in tower.state_dir.joinpath("events.jsonl").read_text(encoding="utf-8")


def test_b3_unstick_as_dropped_closes_it(tower):
    board = Board(tower)
    task = board.create("seo", "give up")
    board.transition(task, "in_progress", note="worker died")
    assert cli.main(["unstick", task.id, "--as", "dropped"]) == 0
    assert board.load(task.id).status == "failed"


def test_b3_status_flags_a_stale_claim(tower, capsys):
    board = Board(tower)
    task = board.create("seo", "orphaned")
    board.transition(task, "in_progress", note="claimed")
    # board.save() re-stamps updated_at, so age the task file directly — which is
    # what a dead worker leaves behind anyway
    task_file = tower.tasks_dir / f"{task.id}.json"
    data = json.loads(task_file.read_text(encoding="utf-8"))
    data["updated_at"] = "2026-11-01T00:00:00+0800"
    task_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "⧗stale" in out


def test_b9_inbox_tolerates_entries_without_role(tower, capsys):
    board = Board(tower)
    board.notify({"event": "bugfix_receipt", "summary": "no role here"})
    assert cli.main(["inbox"]) == 0
    out = capsys.readouterr().out
    assert "no role here" in out


def test_b10_brief_file_reads_utf8_content(tower, capsys):
    brief = tower.workspace / "long-brief.md"
    brief.write_text("核对 <hero> 里的 video 标签（含 < > 与中文）", encoding="utf-8")
    assert cli.main(["dispatch", "seo", "--brief-file", str(brief)]) == 0
    task = Board(tower).all()[-1]
    assert "video 标签" in task.brief and "<hero>" in task.brief


def test_b1_heartbeat_carries_the_config_hash(tower, monkeypatch):
    worker._heartbeat(tower)
    info = json.loads((tower.queue_dir / "worker.json").read_text(encoding="utf-8"))
    assert info.get("cfg_sha") and len(info["cfg_sha"]) == 16

    # a tower.toml change must make ensure_worker retire the old process
    calls = []
    monkeypatch.setattr(worker.subprocess, "Popen",
                        lambda argv, **kw: calls.append(argv) or object())
    monkeypatch.setattr(worker, "ensure_worker", worker._real_ensure_worker)
    assert worker.ensure_worker(tower) is False, "same config → keep the live worker"

    cfg_path = pathlib.Path(tower.config_path)
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    data["documented_change"] = True
    cfg_path.write_text(json.dumps(data), encoding="utf-8")
    # _config_hash reads the file bytes on every call, so the same cfg object
    # sees the change — the heartbeat is now stale and a new worker is spawned
    assert worker.ensure_worker(tower) is True, "config changed → respawn"
    assert calls
