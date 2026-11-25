"""Zombie-task rules for `tower janitor` (T-0062)."""

import json
import os
import time
from pathlib import Path

from control_tower.board import Task
from control_tower.config import ensure_layout, parse_config
from control_tower.janitor import find_zombies


def _ago(seconds: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(time.time() - seconds))


def _tower(tmp_path):
    path = tmp_path / "tower.json"
    path.write_text(json.dumps({
        "name": "janitor",
        "workspace": str(tmp_path / "ws"),
        "roles": {"core": {"adapter": "mock"}, "cli": {"adapter": "mock"}},
        "adapters": {"dsh": {"sessions": {}}},
    }), encoding="utf-8")
    cfg = parse_config(path)
    ensure_layout(cfg)
    return cfg


def _task(cfg, task_id, *, role="core", status="rework", age=90000.0,
          brief="same work", scopes=("src/",), created_at="", due_at=""):
    updated = _ago(age)
    data = Task(
        id=task_id,
        role=role,
        brief=brief,
        status=status,
        created_at=created_at or _ago(age + 3600),
        updated_at=updated,
        write_scopes=list(scopes),
        due_at=due_at,
    )
    (cfg.tasks_dir / f"{task_id}.json").write_text(
        json.dumps(data.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return data


def _heartbeat(cfg, age=0.0):
    cfg.queue_dir.mkdir(parents=True, exist_ok=True)
    heart = cfg.queue_dir / "worker.json"
    heart.write_text("{}", encoding="utf-8")
    stamp = time.time() - age
    os.utime(heart, (stamp, stamp))


def _job(cfg, task_id, job_id="Q-00001"):
    pending = cfg.queue_dir / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    (pending / f"{job_id}.json").write_text(
        json.dumps({"id": job_id, "task": task_id}), encoding="utf-8"
    )


def test_rule1_orphan_role(tmp_path):
    cfg = _tower(tmp_path)
    task = _task(cfg, "T-0001", role="ghost")

    hits = find_zombies(cfg)

    assert [(h.task_id, h.rule, h.action) for h in hits] == [("T-0001", 1, "dropped")]
    assert "ghost" in hits[0].evidence and task.updated_at in hits[0].evidence


def test_rule2_no_executor(tmp_path):
    cfg = _tower(tmp_path)
    _task(cfg, "T-0002")

    hits = find_zombies(cfg)

    assert [(h.task_id, h.rule, h.action) for h in hits] == [("T-0002", 2, "dropped")]
    assert "no pending/claimed job" in hits[0].evidence
    assert "heartbeat missing" in hits[0].evidence


def test_rule2_stale_heartbeat(tmp_path):
    cfg = _tower(tmp_path)
    _task(cfg, "T-0002")
    _heartbeat(cfg, age=900)

    hits = find_zombies(cfg)

    assert hits and hits[0].rule == 2
    assert "heartbeat stale" in hits[0].evidence


def test_rule3_overdue_late(tmp_path):
    cfg = _tower(tmp_path)
    _task(
        cfg, "T-0003", status="late", age=3 * 86400,
        created_at=_ago(10 * 3600), due_at=_ago(9 * 3600),
    )
    _heartbeat(cfg)

    hits = find_zombies(cfg)

    assert [(h.task_id, h.rule) for h in hits] == [("T-0003", 3)]
    assert "late past due_at" in hits[0].evidence


def test_rule4_superseded_by_higher_accepted_task(tmp_path):
    cfg = _tower(tmp_path)
    _task(cfg, "T-0002", brief="old work")
    _task(cfg, "T-0009", status="accepted", age=3600, brief="new work")
    _heartbeat(cfg)

    hits = find_zombies(cfg)

    assert [(h.task_id, h.rule, h.action) for h in hits] == [("T-0002", 4, "dropped")]
    assert "T-0009" in hits[0].evidence


def test_rule5_duplicate_keeps_lowest_id(tmp_path):
    cfg = _tower(tmp_path)
    _task(cfg, "T-0005")
    _task(cfg, "T-0007")
    _heartbeat(cfg)

    hits = find_zombies(cfg)

    assert [(h.task_id, h.rule) for h in hits] == [("T-0007", 5)]
    assert "duplicate of T-0005" in hits[0].evidence


def test_rule6_stale_report_is_advisory_accept(tmp_path):
    cfg = _tower(tmp_path)
    _task(cfg, "T-0006", status="reported", age=3 * 86400)
    _heartbeat(cfg)
    report = cfg.roles_dir / "core" / "T-0006-REPORT.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("## Status\nready\n", encoding="utf-8")
    old = time.time() - 8000
    os.utime(report, (old, old))

    hits = find_zombies(cfg)

    assert [(h.task_id, h.rule, h.action) for h in hits] == [("T-0006", 6, "accept")]
    assert str(report) in hits[0].evidence


def test_pending_and_accepted_are_never_returned(tmp_path):
    cfg = _tower(tmp_path)
    _task(cfg, "T-0001", status="pending", age=5 * 86400)
    _task(cfg, "T-0002", status="accepted", age=5 * 86400)

    assert find_zombies(cfg) == []


def test_fresh_reported_is_not_flagged(tmp_path):
    cfg = _tower(tmp_path)
    _task(cfg, "T-0009", status="reported", age=3600)

    assert find_zombies(cfg) == []


def test_limit_caps_results(tmp_path):
    cfg = _tower(tmp_path)
    for n in range(1, 4):
        _task(cfg, f"T-000{n}", role="ghost", brief=f"orphan {n}")

    assert len(find_zombies(cfg, limit=2)) == 2


def test_older_than_is_respected(tmp_path):
    cfg = _tower(tmp_path)
    _task(cfg, "T-0002", age=2 * 3600)

    assert find_zombies(cfg, older_than_s=3 * 3600) == []
    assert [h.rule for h in find_zombies(cfg, older_than_s=3600)] == [2]


def test_missing_or_corrupt_queue_is_tolerated(tmp_path):
    cfg = _tower(tmp_path)
    _task(cfg, "T-0002")

    assert [h.rule for h in find_zombies(cfg)] == [2]

    pending = cfg.queue_dir / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    (pending / "Q-00001.json").write_text("{not json", encoding="utf-8")

    assert [h.rule for h in find_zombies(cfg)] == [2]
