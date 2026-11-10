"""Queue supervision + parallel dispatch (2026-11-10 field request)."""

import json
import pathlib

import pytest

from control_tower import cli, worker
from control_tower.board import Board
from control_tower.config import ensure_layout, parse_config
from control_tower.verify import split_file_candidates


@pytest.fixture()
def tower(tmp_path, monkeypatch):
    config = {
        "name": "queue-tower",
        "workspace": str(tmp_path),
        "roles": {"seo": {"description": "s", "adapter": "mock", "max_rounds": 1},
                  "auditor": {"description": "a", "adapter": "mock"}},
        "review_roles": ["auditor"],
    }
    path = tmp_path / "tower.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    cfg = parse_config(path)
    ensure_layout(cfg)
    monkeypatch.chdir(tmp_path)
    return cfg


def _pending(cfg):
    return list((cfg.queue_dir / "pending").glob("*.json"))


def test_enqueue_auto_starts_a_consumer(tower):
    spawns = []
    # conftest already stubs ensure_worker; record the call explicitly
    original = worker.ensure_worker

    def tracked(cfg):
        spawns.append(str(cfg.workspace))
        worker._heartbeat(cfg)
        return True

    worker.ensure_worker = tracked
    try:
        job = worker.enqueue(tower, "dispatch", "T-0001")
    finally:
        worker.ensure_worker = original
    assert spawns == [str(tower.workspace)], "enqueue must ensure a worker"
    assert job["spawned"] is True


def test_worker_status_and_backlog_alarm(tower):
    worker.enqueue(tower, "adversarial", "T-0001")
    (tower.queue_dir / "worker.json").unlink()      # the consumer died
    status = worker.worker_status(tower)
    assert status["alive"] is False
    assert status["pending"] == 1
    warning = worker.backlog_warning(tower)
    assert warning and "NO worker alive" in warning
    # status surfaces it too
    assert cli.main(["status"]) == 0


def test_worker_status_cli_exits_nonzero_without_consumer(tower, capsys):
    worker.enqueue(tower, "adversarial", "T-0001")
    (tower.queue_dir / "worker.json").unlink()
    code = cli.main(["worker", "--status"])
    out = capsys.readouterr().out
    assert code == 2
    assert "queue backlog" in out and "pending 1" in out


def test_dispatch_parallel_queues_every_brief_without_waiting(tower, tmp_path):
    a = tmp_path / "brief-a.md"
    b = tmp_path / "brief-b.md"
    a.write_text("第一单：图廊修复", encoding="utf-8")
    b.write_text("第二单：Banner 修复", encoding="utf-8")

    assert cli.main(["dispatch", "seo", "--brief-file", str(a),
                     "--brief-file", str(b)]) == 0
    board = Board(tower)
    tasks = [t for t in board.all() if t.role == "seo"]
    assert len(tasks) == 2, "one task per brief"
    assert {t.brief for t in tasks} == {"第一单：图廊修复", "第二单：Banner 修复"}
    assert all(t.mode == "async" for t in tasks)
    jobs = [json.loads(p.read_text(encoding="utf-8")) for p in _pending(tower)]
    assert {j["task"] for j in jobs} == {t.id for t in tasks}


def test_dispatch_parallel_single_brief(tower):
    assert cli.main(["dispatch", "seo", "单单也并行提交", "--parallel"]) == 0
    (task,) = [t for t in Board(tower).all() if t.role == "seo"]
    assert task.mode == "async"


def test_review_checks_the_consumer(tower, monkeypatch, capsys):
    board = Board(tower)
    task = board.create("seo", "review me")
    board.transition(task, "reported", note="filed")
    monkeypatch.setattr(worker, "ensure_worker", lambda cfg: False)
    monkeypatch.setattr(worker, "worker_alive", lambda cfg: False)
    code = cli.main(["review", task.id])
    out = capsys.readouterr().out
    assert code == 1, "no consumer must be a loud failure, not silent queueing"
    assert "NO worker" in out


def test_files_middle_dot_list_is_split(tmp_path, monkeypatch):
    (tmp_path / "a.json").write_text("{}", encoding="utf-8")
    (tmp_path / "b.json").write_text("{}", encoding="utf-8")
    assert split_file_candidates("a.json·b.json") == ["a.json", "b.json"]
