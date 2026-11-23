"""Task groups: one unit of work, one room (v3 §4).

Without a boundary every task of the day shares one conversation, so each bot
re-reads history that has nothing to do with its job. A group is that boundary:

    created → discussing → working → reviewing → closed

It is opened by `tower kickoff` (with a task package) or by the first dispatch
that names it, and it is closed when the work is accepted, reworked away or
abandoned. Closing archives the room under `.tower/conversations/` so the record
survives while the session stops being woken up.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .config import TowerConfig
from .board import now_ts

STATUSES = ("created", "discussing", "working", "reviewing", "closed")


@dataclass
class Group:
    id: str
    title: str
    roles: list[str] = field(default_factory=list)
    tasks: list[str] = field(default_factory=list)
    status: str = "created"
    package: str = ""          # package id (P-000N) once kickoff wrote one
    created_at: str = field(default_factory=now_ts)
    updated_at: str = field(default_factory=now_ts)
    closed_at: str = ""
    close_reason: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Group":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


def groups_dir(cfg: TowerConfig) -> Path:
    path = cfg.groups_dir
    path.mkdir(parents=True, exist_ok=True)
    return path


def group_dir(cfg: TowerConfig, group_id: str) -> Path:
    path = groups_dir(cfg) / group_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _next_id(cfg: TowerConfig) -> str:
    existing = [p.name for p in groups_dir(cfg).iterdir() if p.is_dir()]
    numbers = [int(name[2:]) for name in existing if name.startswith("G-") and name[2:].isdigit()]
    return f"G-{max(numbers, default=0) + 1:04d}"


def save(cfg: TowerConfig, group: Group) -> Path:
    group.updated_at = now_ts()
    path = group_dir(cfg, group.id) / "group.json"
    path.write_text(json.dumps(group.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def load(cfg: TowerConfig, group_id: str) -> Group:
    path = group_dir(cfg, group_id) / "group.json"
    if not path.exists():
        raise FileNotFoundError(f"no such group: {group_id}")
    return Group.from_dict(json.loads(path.read_text(encoding="utf-8")))


def create(cfg: TowerConfig, title: str, roles: list[str] | None = None) -> Group:
    group = Group(id=_next_id(cfg), title=title or "untitled", roles=list(roles or []))
    group.history.append({"ts": group.created_at, "event": "created", "title": group.title})
    save(cfg, group)
    return group


def all_groups(cfg: TowerConfig) -> list[Group]:
    out: list[Group] = []
    for path in sorted(groups_dir(cfg).glob("G-*/group.json")):
        try:
            out.append(Group.from_dict(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError, TypeError):
            continue
    return out


def live(cfg: TowerConfig) -> list[Group]:
    return [g for g in all_groups(cfg) if g.status != "closed"]


def for_task(cfg: TowerConfig, task_id: str) -> Group | None:
    for group in all_groups(cfg):
        if task_id in group.tasks:
            return group
    return None


def attach(cfg: TowerConfig, group_id: str, task_id: str) -> Group:
    group = load(cfg, group_id)
    if task_id not in group.tasks:
        group.tasks.append(task_id)
        group.history.append({"ts": now_ts(), "event": "task_attached", "task": task_id})
    if group.status in ("created", "discussing"):
        group.status = "working"
    save(cfg, group)
    return group


def set_status(cfg: TowerConfig, group_id: str, status: str, note: str = "") -> Group:
    if status not in STATUSES:
        raise ValueError(f"unknown group status: {status}")
    group = load(cfg, group_id)
    group.status = status
    group.history.append({"ts": now_ts(), "event": "status", "status": status, "note": note})
    save(cfg, group)
    return group


def close(cfg: TowerConfig, group_id: str, reason: str = "") -> Group:
    """Close the room and archive it; the session is not woken again."""
    group = load(cfg, group_id)
    group.status = "closed"
    group.closed_at = now_ts()
    group.close_reason = reason
    group.history.append({"ts": group.closed_at, "event": "closed", "reason": reason})
    save(cfg, group)
    cfg.conversations_dir.mkdir(parents=True, exist_ok=True)
    archive = cfg.conversations_dir / f"{group.id}-closed.md"
    lines = [
        f"# {group.id} {group.title}",
        "",
        f"- status: closed ({reason or 'no reason given'})",
        f"- roles: {', '.join(group.roles) or '—'}",
        f"- tasks: {', '.join(group.tasks) or '—'}",
        f"- opened: {group.created_at}",
        f"- closed: {group.closed_at}",
        f"- package: {group.package or '—'}",
        "",
        "## History",
        "",
    ]
    lines += [f"- {row.get('ts')} {row.get('event')} {row.get('note') or row.get('task') or ''}".rstrip()
              for row in group.history]
    archive.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return group
