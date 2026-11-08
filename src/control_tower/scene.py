"""Scene data: the same tower, shaped for an isometric control room.

Nothing here invents state. Every number comes from `Board` (tasks + ledger), the
way `docs/board-model.md` requires; the only estimate is money, and it says so.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .board import (
    Board,
    STATUS_ACCEPTED,
    STATUS_FAILED,
    STATUS_IN_PROGRESS,
    STATUS_REPORTED,
    now_ts,
)
from .config import TowerConfig

# USD per adapter run. A run is one model turn against one role; the real bill is
# per-token and not knowable from the tower's files, so this stays a stated rate.
RATE_USD = 0.12
MAX_PENDING = 4
MAX_STUCK = 2

FLOOR_STYLE = {
    "ops": {"dept": "Operations", "accent": "#14b8a6"},
    "research": {"dept": "Research", "accent": "#22c55e"},
    "content": {"dept": "Content", "accent": "#ec4899"},
    "creative": {"dept": "Creative", "accent": "#f59e0b"},
    "frontend": {"dept": "Code", "accent": "#3b82f6"},
    "backend": {"dept": "Code", "accent": "#6366f1"},
    "qa": {"dept": "Quality", "accent": "#a855f7"},
    "auditor": {"dept": "Quality", "accent": "#8b5cf6"},
    "seo": {"dept": "Growth", "accent": "#0891b2"},
    "planner": {"dept": "Strategy", "accent": "#64748b"},
    "content_writer": {"dept": "Content", "accent": "#db2777"},
    "writer": {"dept": "Content", "accent": "#db2777"},
}
DEFAULT_STYLE = {"dept": "Operations", "accent": "#64748b"}


def _age_s(text: str) -> int | None:
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            parsed = datetime.strptime((text or "").strip(), fmt)
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone().replace(tzinfo=None)
            return max(0, int((datetime.now() - parsed).total_seconds()))
        except ValueError:
            continue
    return None


def _style(role: str) -> dict[str, str]:
    return FLOOR_STYLE.get(role, DEFAULT_STYLE)


def _status(task) -> str:
    """The four ring colours, in the order the board defines them."""
    if task is None:
        return "resting"
    if task.status == STATUS_FAILED or task.state == "failed":
        return "stuck"
    if task.state == "blocked" or (task.attention or {}).get("question"):
        return "waiting"
    if task.status == STATUS_IN_PROGRESS:
        return "working"
    if task.status in (STATUS_REPORTED, "rework"):
        return "waiting"
    return "resting"


def _steps(task) -> list[dict[str, Any]]:
    """Steps derived from the task's own history — never fabricated."""
    steps: list[dict[str, Any]] = [{"label": "dispatched", "done": True}]
    seen = {h.get("status") for h in (task.history or [])}
    if "report" in str(seen).lower() or task.report:
        steps.append({"label": "report filed", "done": bool(task.report)})
    if "accepted" in seen:
        steps.append({"label": "verified", "done": True})
    elif task.status == STATUS_REPORTED:
        steps.append({"label": "verified", "done": False})
    if "rework" in seen:
        steps.append({"label": "rework", "done": True})
    return steps


def _cost(task) -> dict[str, Any]:
    runs = len(task.assignments or [])
    return {"runs": runs, "est_usd": round(runs * RATE_USD, 2)}


def build_scene(cfg: TowerConfig, board: Board | None = None,
                project_color: str = "#ea7a0c") -> dict[str, Any]:
    """One payload per refresh. Deterministic, cheap, no I/O beyond the tower files."""
    board = board or Board(cfg)
    tasks = board.all()
    now = datetime.now()

    by_role: dict[str, list] = {role: [] for role in cfg.roles}
    for task in tasks:
        by_role.setdefault(task.role, []).append(task)

    floors = []
    agents = []
    for index, (role_name, role) in enumerate(cfg.roles.items()):
        style = _style(role_name)
        mine = sorted(by_role.get(role_name, []), key=lambda t: t.updated_at, reverse=True)
        active = next((t for t in mine if t.status in (STATUS_IN_PROGRESS, STATUS_REPORTED)), None)
        blocked = next((t for t in mine if t.state == "blocked"), None)
        failed = next((t for t in mine if t.status == STATUS_FAILED), None)
        focus = active or blocked or failed or (mine[0] if mine else None)
        status = _status(blocked or failed or active)
        cost = _cost(focus) if focus else {"runs": 0, "est_usd": 0.0}
        floors.append({
            "id": role_name,
            "dept": style["dept"],
            "level": index + 1,
            "agents": 1,
            "status": status,
            "tasks": len(mine),
            "open": sum(1 for t in mine if t.status not in (STATUS_ACCEPTED, STATUS_FAILED)),
        })
        agents.append({
            "id": role_name,
            "name": role_name,
            "floor": index + 1,
            "dept": style["dept"],
            "accent": style["accent"],
            "model": role.adapter,
            "status": status,
            "task": None if focus is None else {
                "id": focus.id,
                "brief": focus.brief,
                "status": focus.status,
                "state": focus.state,
                "steps": _steps(focus),
                "age_s": _age_s(focus.updated_at),
                "due": focus.due_at,
                "cost": cost,
            },
            "tasks": len(mine),
            "cost": {"runs": sum(len(t.assignments or []) for t in mine),
                     "est_usd": round(sum(len(t.assignments or []) for t in mine) * RATE_USD, 2)},
        })

    pending = []
    for task in board.needs_person():
        if task.state not in ("blocked", "failed") and task.status != STATUS_FAILED:
            continue
        question = (task.attention or {}).get("question", "")
        if not question:
            for entry in reversed(task.history or []):
                note = str(entry.get("note") or "")
                if "exit" in note.lower() or "fail" in note.lower() or "verif" in note.lower():
                    question = note
                    break
            if not question and task.report:
                checks = task.report.get("checks") or []
                question = "verification failed" + (f": {checks[0][:80]}" if checks else "")
            if not question:
                question = f"{task.status} — no question recorded on the task"
        pending.append({
            "task": task.id,
            "agent": task.role,
            "kind": "stuck" if (task.status == STATUS_FAILED or task.state == "failed") else "question",
            "reason": question or (task.report or {}).get("summary", "")[:160] or "task needs a decision",
            "fix": ("tower triage" if task.status == STATUS_FAILED else f"reply to {task.id}"),
        })
    stuck_total = sum(1 for p in pending if p["kind"] == "stuck")
    stuck_cards = [p for p in pending if p["kind"] == "stuck"][:MAX_STUCK]
    question_cards = [p for p in pending if p["kind"] != "stuck"]
    pending = stuck_cards + question_cards[:max(0, MAX_PENDING - len(stuck_cards))]

    scheduled = []
    for task in tasks:
        if not task.due_at or task.status == STATUS_ACCEPTED:
            continue
        due_dt = None
        for fmt in ("%Y-%m-%dT%H:%M:%S",):
            try:
                due_dt = datetime.strptime(task.due_at, fmt)
            except ValueError:
                pass
        if due_dt is None:
            continue
        scheduled.append({
            "task": task.id,
            "agent": task.role,
            "due": task.due_at,
            "in_s": int((due_dt - now).total_seconds()),
        })
    scheduled.sort(key=lambda item: item["in_s"])

    today = now.date().isoformat()
    completed = []
    for task in tasks:
        if task.status != STATUS_ACCEPTED:
            continue
        accepted = next((h for h in (task.history or []) if h.get("status") == "accepted"), None)
        if accepted is None or not str(accepted.get("ts", "")).startswith(today):
            continue
        completed.append({
            "task": task.id,
            "agent": task.role,
            "brief": task.brief,
            "cost": _cost(task),
            "age_s": _age_s(accepted.get("ts", "")),
        })

    per_floor = {floor["id"]: {"est_usd": 0.0, "runs": 0} for floor in floors}
    for agent in agents:
        per_floor[agent["id"]] = {"est_usd": agent["cost"]["est_usd"], "runs": agent["cost"]["runs"]}
    total_runs = sum(item["runs"] for item in per_floor.values())
    top = max(agents, key=lambda a: a["cost"]["runs"], default=None)

    events = []
    try:
        lines = cfg.ledger_path.read_text(encoding="utf-8").splitlines()[-60:]
    except OSError:
        lines = []
    import json as _json

    for raw in lines:
        try:
            event = _json.loads(raw)
        except ValueError:
            continue
        events.append({
            "ts": str(event.get("ts", ""))[-14:],
            "event": str(event.get("event", "")),
            "task": str(event.get("task", "")),
            "role": str(event.get("role", "")),
            "floor": next((f["level"] for f in floors if f["id"] == event.get("role")), 0),
        })

    return {
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "project": {"name": cfg.name, "path": str(cfg.workspace), "color": project_color},
        "currency": "USD",
        "rate_usd": RATE_USD,
        "floors": floors,
        "agents": agents,
        "pending": pending[:MAX_PENDING],
        "pending_total": len(pending),
        "stuck_total": stuck_total,
        "scheduled": scheduled[:6],
        "completed_today": completed[:20],
        "cost": {
            "est_usd": round(total_runs * RATE_USD, 2),
            "runs": total_runs,
            "by_floor": per_floor,
            "top_burner": {"agent": top["id"], "est_usd": top["cost"]["est_usd"]} if top else None,
        },
        "log": list(reversed(events)),
        "kpis": {
            "agents": len(agents),
            "working": sum(1 for a in agents if a["status"] == "working"),
            "waiting": sum(1 for a in agents if a["status"] == "waiting"),
            "stuck": sum(1 for a in agents if a["status"] == "stuck"),
            "resting": sum(1 for a in agents if a["status"] == "resting"),
            "completed_today": len(completed),
        },
        "generated_ts": now_ts(),
    }
