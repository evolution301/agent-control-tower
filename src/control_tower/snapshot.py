"""One snapshot of the tower, shaped for humans first.

The panel and the terminal board are two renderings of this single structure, so
they can never disagree. Design rule (borrowed from dsh-superagent): the things
that need a person come first; healthy state takes no space.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .board import PERSON_STATES, Board, Task
from .board import attention_status
from .config import TowerConfig
from .supervise import due_human


def age_s(ts: str) -> int | None:
    """Seconds since an ISO-ish timestamp we wrote ourselves."""
    if not ts:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            parsed = time.mktime(time.strptime(ts, fmt))
            if fmt == "%Y-%m-%dT%H:%M:%S%z":
                # strptime with %z yields a struct_time already in local terms
                pass
            return max(0, int(time.time() - parsed))
        except ValueError:
            continue
    return None


def _human_age(seconds: int | None) -> str:
    if seconds is None:
        return "—"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def _last_event(board: Board, task: Task, kind: str) -> str:
    """Most recent timestamp for a task from its own history."""
    for entry in reversed(task.history or []):
        if entry.get("status") == kind or entry.get("ts"):
            return entry.get("ts", "")
    return task.updated_at


@dataclass
class RoleView:
    name: str
    description: str = ""
    adapter: str = ""
    doing: str = "idle"          # working / waiting / idle
    task_id: str = ""
    brief: str = ""
    since: str = ""
    age: str = "—"


@dataclass
class Snapshot:
    name: str = ""
    workspace: str = ""
    generated_at: str = ""
    counts: dict[str, int] = field(default_factory=dict)
    attention: list[dict[str, Any]] = field(default_factory=list)
    triage: list[dict[str, Any]] = field(default_factory=list)
    in_flight: list[dict[str, Any]] = field(default_factory=list)
    roles: list[dict[str, Any]] = field(default_factory=list)
    recent: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "workspace": self.workspace,
            "generated_at": self.generated_at,
            "counts": self.counts,
            "attention": self.attention,
            "triage": self.triage,
            "in_flight": self.in_flight,
            "roles": self.roles,
            "recent": self.recent,
        }


def build_snapshot(cfg: TowerConfig, board: Board | None = None, recent: int = 8) -> Snapshot:
    board = board or Board(cfg)
    tasks = board.all()
    by_state: dict[str, int] = {}
    for t in tasks:
        key = t.state or t.status
        by_state[key] = by_state.get(key, 0) + 1

    attention = []
    triage = []
    in_flight = []
    # One source of truth: the Board decides who owns a task, the snapshot only
    # renders that decision. Anything else drifts (a restating task carrying an
    # attention question once counted as "needs you" while the badge said "tower").
    person_ids = {t.id for t in board.needs_person()}
    claimed_ids = {t.id for t in tasks
                   if attention_status(t) == "claimed" and t.status != "accepted"}
    resolved_ids = {t.id for t in tasks if attention_status(t) == "resolved"}
    lead_ids = {t.id for t in board.needs_lead()}
    for t in tasks:
        state = t.state or ""
        question = (t.attention or {}).get("question", "")
        item = {
            "id": t.id,
            "role": t.role,
            "state": state or t.status,
            "status": t.status,
            "brief": (t.brief or "").strip(),
            "round": t.round,
            "max_rounds": t.max_rounds,
            "question": question,
            "updated_at": t.updated_at,
            "age": _human_age(age_s(t.updated_at)),
            "due": due_human(t),
            "overdue": bool(t.overdue) or due_human(t).startswith("overdue"),
        }
        if t.status == "accepted":
            continue  # done is done: an accepted task must never sit in a queue
        if t.id in claimed_ids or t.id in resolved_ids:
            continue  # the issues bot owns it (处理中 / 已处理), not 「需要你」
        if t.id in person_ids:
            attention.append(item)          # a human really has to decide
        elif t.id in lead_ids:
            triage.append(item)             # the tower/lead can close this itself
        elif t.status in ("pending", "claimed", "in_progress"):
            in_flight.append(item)

    # Role view: what is this role doing right now?
    roles = []
    for name, role in cfg.roles.items():
        mine = [t for t in tasks if t.role == name]
        active = [t for t in mine if t.status in ("claimed", "in_progress")]
        queued = [t for t in mine if t.status in ("pending", "rework", "reported")]
        if active:
            t = active[-1]
            roles.append(RoleView(name, role.description, role.adapter, "working", t.id,
                                  (t.brief or "")[:120], t.updated_at, _human_age(age_s(t.updated_at))).__dict__)
        elif queued:
            t = queued[-1]
            roles.append(RoleView(name, role.description, role.adapter, "waiting", t.id,
                                  (t.brief or "")[:120], t.updated_at, _human_age(age_s(t.updated_at))).__dict__)
        else:
            roles.append(RoleView(name, role.description, role.adapter).__dict__)

    # Ledger tail (newest last -> reverse for display)
    recent_items = []
    if cfg.ledger_path.exists():
        lines = cfg.ledger_path.read_text(encoding="utf-8").splitlines()[-recent * 3:]
        import json as _json

        parsed = []
        for line in lines:
            try:
                parsed.append(_json.loads(line))
            except Exception:
                continue
        for e in parsed[-recent:]:
            recent_items.append({
                "ts": e.get("ts", ""),
                "event": e.get("event", "?"),
                "task": e.get("task", ""),
                "role": e.get("role", ""),
                "note": str(e.get("note", ""))[:160],
            })
        recent_items.reverse()

    counts = {
        "total": len(tasks),
        "attention": len(attention),
        # v0.12: the issues bot owns claimed/resolved work — the panel says so
        "attention_claimed": len(claimed_ids),
        "attention_resolved": len(resolved_ids),
        "triage": len(triage),
        "in_flight": len(in_flight),
        # design doc §3: the panel/supervise show in-flight async work explicitly
        "async_in_flight": sum(
            1 for t in tasks
            if getattr(t, "mode", "") == "async" and t.status != "accepted"
        ),
        **{k: v for k, v in sorted(by_state.items())},
    }

    return Snapshot(
        name=cfg.name,
        workspace=str(cfg.workspace),
        generated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        counts=counts,
        attention=attention,
        triage=triage,
        in_flight=in_flight,
        roles=roles,
        recent=recent_items,
    )
