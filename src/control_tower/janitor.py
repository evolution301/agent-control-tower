"""Zombie-task detection for `tower janitor` (T-0062).

A zombie is a task that is still on the board, has not moved for
``older_than_s``, and matches one of six auditable rules. Nothing here mutates
the board: hits are evidence plus a suggested action, and the CLI decides.
"""

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .board import Board, Task, age_seconds
from .config import TowerConfig

HEARTBEAT_STALE_S = 600.0
REPORT_STALE_S = 7200.0
LATE_FALLBACK_S = 7200.0
NEVER_TOUCH = frozenset(
    {"pending", "in_progress", "accepted", "closed", "failed", "dropped"}
)


@dataclass
class ZombieHit:
    task_id: str
    role: str
    status: str
    rule: int
    evidence: str
    action: str           # "dropped" | "accept"


def _parse_ts(ts: str) -> datetime | None:
    try:
        parsed = datetime.strptime((ts or "").strip(), "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _stamp(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).astimezone().isoformat(timespec="seconds")


def _task_number(task_id: str) -> int:
    digits = re.sub(r"\D", "", task_id or "")
    return int(digits) if digits else -1


def _scopes(task: Task) -> list[str]:
    return [
        str(scope).replace("\\", "/").strip().strip("/")
        for scope in (task.write_scopes or [])
        if str(scope).strip()
    ]


def _overlap(left: list[str], right: list[str]) -> str:
    for a in left:
        for b in right:
            if a == b or a.startswith(b + "/") or b.startswith(a + "/"):
                return f"{a!r} overlaps {b!r}"
    return ""


def _heartbeat(cfg: TowerConfig) -> tuple[bool, str]:
    """(dead, evidence): missing or older than HEARTBEAT_STALE_S counts as dead."""
    heart = cfg.queue_dir / "worker.json"
    try:
        mtime = heart.stat().st_mtime
    except OSError:
        return True, f"heartbeat missing ({heart})"
    age = time.time() - mtime
    if age > HEARTBEAT_STALE_S:
        return True, (
            f"heartbeat stale ({heart} mtime {_stamp(mtime)}, "
            f"{age:.0f}s > {HEARTBEAT_STALE_S:.0f}s)"
        )
    return False, ""


def _queued_tasks(cfg: TowerConfig) -> set[str]:
    """Task ids with a job in pending/ or claimed/; missing dirs are empty."""
    found: set[str] = set()
    for folder in (cfg.queue_dir / "pending", cfg.queue_dir / "claimed"):
        if not folder.is_dir():
            continue
        for path in folder.glob("*.json"):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(job, dict) and job.get("task"):
                found.add(str(job["task"]))
    return found


def _late_deadline(task: Task) -> tuple[datetime, str] | None:
    due = _parse_ts(task.due_at)
    if due is None:
        return None
    created = _parse_ts(task.created_at)
    if created is None:
        return due + timedelta(seconds=LATE_FALLBACK_S), "created_at unparseable; 2h fallback"
    return due + 2 * (due - created), f"created_at {task.created_at}"


def _superseder(task: Task, tasks: list[Task]) -> tuple[Task, str] | None:
    mine = _parse_ts(task.updated_at)
    if mine is None:
        return None
    best: tuple[Task, str] | None = None
    for other in tasks:
        if other.status != "accepted" or other.role != task.role:
            continue
        if _task_number(other.id) <= _task_number(task.id):
            continue
        overlap = _overlap(_scopes(task), _scopes(other))
        if not overlap:
            continue
        theirs = _parse_ts(other.updated_at)
        if theirs is None or theirs <= mine:
            continue
        if best is None or _task_number(other.id) > _task_number(best[0].id):
            best = (other, overlap)
    return best


def _duplicate_canonical(task: Task, tasks: list[Task]) -> Task | None:
    key = (task.role, (task.brief or "")[:80], tuple(sorted(_scopes(task))))
    group = [
        other for other in tasks
        if (other.role, (other.brief or "")[:80], tuple(sorted(_scopes(other)))) == key
    ]
    if len(group) < 2:
        return None
    canonical = min(group, key=lambda item: _task_number(item.id))
    return None if canonical.id == task.id else canonical


def _report_age(cfg: TowerConfig, task: Task) -> tuple[float, Path] | None:
    path = cfg.roles_dir / task.role / f"{task.id}-REPORT.md"
    try:
        return time.time() - path.stat().st_mtime, path
    except OSError:
        return None


def _rule_hit(
    cfg: TowerConfig,
    task: Task,
    tasks: list[Task],
    queued: set[str],
    heartbeat_dead: bool,
    heartbeat_note: str,
    now: datetime,
) -> ZombieHit | None:
    role = task.role
    updated = task.updated_at
    task_file = cfg.tasks_dir / f"{task.id}.json"

    if role not in cfg.roles:
        return ZombieHit(
            task.id, role, task.status, 1,
            f"{task_file}: role {role!r} is not in cfg.roles (known: "
            f"{', '.join(sorted(cfg.roles))}); status {task.status}, updated_at {updated}",
            "dropped",
        )

    if task.status in ("rework", "reported", "late") and task.id not in queued and heartbeat_dead:
        return ZombieHit(
            task.id, role, task.status, 2,
            f"{task_file}: no pending/claimed job for {task.id} under {cfg.queue_dir}; "
            f"{heartbeat_note}; status {task.status}, updated_at {updated}",
            "dropped",
        )

    if task.status == "late":
        deadline = _late_deadline(task)
        if deadline is not None and now > deadline[0]:
            return ZombieHit(
                task.id, role, task.status, 3,
                f"{task_file}: late past due_at {task.due_at} ({deadline[1]}); "
                f"deadline {deadline[0].isoformat(timespec='seconds')} < now "
                f"{now.isoformat(timespec='seconds')}, updated_at {updated}",
                "dropped",
            )

    superseder = _superseder(task, tasks)
    if superseder is not None:
        other, overlap = superseder
        other_file = cfg.tasks_dir / f"{other.id}.json"
        return ZombieHit(
            task.id, role, task.status, 4,
            f"{task_file}: {other.id} accepted at {other.updated_at} "
            f"({other_file}) supersedes this task "
            f"(updated_at {updated}); {overlap}",
            "dropped",
        )

    canonical = _duplicate_canonical(task, tasks)
    if canonical is not None:
        canonical_file = cfg.tasks_dir / f"{canonical.id}.json"
        return ZombieHit(
            task.id, role, task.status, 5,
            f"{task_file}: duplicate of {canonical.id} "
            f"({canonical_file}; same role, first 80 chars "
            f"of brief, write_scopes {_scopes(task)}); updated_at {updated}",
            "dropped",
        )

    if task.status == "reported":
        report = _report_age(cfg, task)
        if report is not None and report[0] > REPORT_STALE_S:
            age, path = report
            return ZombieHit(
                task.id, role, task.status, 6,
                f"{path} mtime {_stamp(path.stat().st_mtime)} is {age:.0f}s old "
                f"(> {REPORT_STALE_S:.0f}s) and nobody accepted; updated_at {updated}",
                "accept",
            )

    return None


def find_zombies(
    cfg: TowerConfig,
    *,
    older_than_s: float = 86400.0,
    statuses=("rework", "reported", "late"),
    limit: int = 50,
) -> list[ZombieHit]:
    """Tasks that match a zombie rule and have not moved for `older_than_s`."""
    wanted = {str(status) for status in statuses}
    tasks = Board(cfg).all()
    queued = _queued_tasks(cfg)
    heartbeat_dead, heartbeat_note = _heartbeat(cfg)
    now = datetime.now().astimezone()

    hits: list[ZombieHit] = []
    for task in tasks:
        if task.status in NEVER_TOUCH or task.status not in wanted:
            continue
        age = age_seconds(task.updated_at)
        if age is None or age <= older_than_s:
            continue
        hit = _rule_hit(
            cfg, task, tasks, queued, heartbeat_dead, heartbeat_note, now
        )
        if hit is not None:
            hits.append(hit)

    hits.sort(key=lambda hit: (hit.rule, hit.task_id))
    return hits[: max(0, int(limit))]
