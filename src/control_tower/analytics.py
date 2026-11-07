"""Business intelligence over the ledger: what the tower actually got done.

The panel answers "what needs me right now"; this module answers the other
question a manager asks — *is this team working, and is it working well?* It reads
only files the tower already writes (`.tower/tasks/*.json`, `ledger.jsonl`,
`conversations/`, `huddles/`, `roles/*/skills/`) so there is no database, no
telemetry and nothing to install.

Definitions are deliberately stated, because a rate without a denominator is a
story, not a measurement:

* **lead time** — created → the first `accepted` transition, in seconds.
* **verified rate** — accepted / (accepted + reworked), i.e. how often delivered
  work survives `tower accept` on the first try. A tower with no verdicts reports
  no rate (`None`), never 0 %.
* **throughput** — accepted tasks per calendar day, oldest first.
* **rework** — tasks that came back at least once (round > 0).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .board import (
    Board,
    STATUS_ACCEPTED,
    STATUS_FAILED,
    STATUS_RESTATING,
    STATUS_REPORTED,
    STATUS_REWORK,
    Task,
)
from .config import TowerConfig

TS_FMT = "%Y-%m-%dT%H:%M:%S%z"


def _ts(text: str) -> datetime | None:
    for fmt in (TS_FMT, "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S+08:00"):
        try:
            parsed = datetime.strptime((text or "").strip(), fmt)
            return parsed
        except ValueError:
            continue
    return None


def _accepted_at(task: Task) -> datetime | None:
    for entry in task.history or []:
        if entry.get("status") == STATUS_ACCEPTED:
            return _ts(entry.get("ts", ""))
    return None


def lead_time_s(task: Task) -> float | None:
    """Seconds from creation to the first acceptance, or None if never accepted."""
    accepted = _accepted_at(task)
    created = _ts(task.created_at)
    if accepted is None or created is None:
        return None
    # timestamps carry `%z`; if one parsed naive, compare in the same frame
    if (accepted.tzinfo is None) != (created.tzinfo is None):
        if accepted.tzinfo is not None:
            accepted = accepted.astimezone(timezone.utc).replace(tzinfo=None)
        if created.tzinfo is not None:
            created = created.astimezone(timezone.utc).replace(tzinfo=None)
    return max(0.0, (accepted - created).total_seconds())


def percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile — obvious and stable for the small samples here."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = int(round((pct / 100) * (len(ordered) - 1)))
    return ordered[max(0, min(len(ordered) - 1, rank))]


def _count_json(directory: Path) -> int:
    return len(list(directory.glob("*.json"))) if directory.exists() else 0


def _conversation_stats(cfg: TowerConfig) -> dict[str, Any]:
    messages = 0
    undelivered = 0
    for path in sorted(cfg.conversations_dir.glob("C-*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for message in record.get("messages", []):
            messages += 1
            if message.get("delivered") is False:
                undelivered += 1
    return {
        "conversations": _count_json(cfg.conversations_dir),
        "messages": messages,
        "undelivered": undelivered,
    }


def _huddle_stats(cfg: TowerConfig) -> dict[str, Any]:
    huddles = 0
    turns = 0
    for path in sorted(cfg.huddles_dir.glob("H-*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        huddles += 1
        turns += len(record.get("turns", []))
    return {"huddles": huddles, "turns": turns}


def _skill_counts(cfg: TowerConfig) -> dict[str, int]:
    out: dict[str, int] = {}
    for role in cfg.roles:
        directory = cfg.role_dir(role) / "skills"
        out[role] = len(list(directory.glob("*.md"))) if directory.exists() else 0
        out[role] -= 1 if (directory / "INDEX.md").exists() else 0
    return out


def build_report(cfg: TowerConfig, board: Board | None = None, days: int = 14,
                 now: datetime | None = None) -> dict[str, Any]:
    """One dict with every number the BI view shows. Safe on an empty tower."""
    board = board or Board(cfg)
    now = now or datetime.now()
    tasks = board.all()
    skills = _skill_counts(cfg)

    verdicts = {
        "accepted": sum(1 for t in tasks if t.status == STATUS_ACCEPTED),
        "rework": sum(1 for t in tasks if t.status == STATUS_REWORK),
        "failed": sum(1 for t in tasks if t.status == STATUS_FAILED),
        "reported": sum(1 for t in tasks if t.status == STATUS_REPORTED),
        "restating": sum(1 for t in tasks if t.status == STATUS_RESTATING),
        "overdue": sum(1 for t in tasks if t.overdue),
    }
    decided = verdicts["accepted"] + verdicts["rework"]
    verified_rate = (verdicts["accepted"] / decided) if decided else None

    leads = [value for value in (lead_time_s(t) for t in tasks) if value is not None]
    lead = {
        "count": len(leads),
        "p50_s": percentile(leads, 50),
        "p90_s": percentile(leads, 90),
        "avg_s": (sum(leads) / len(leads)) if leads else None,
    }

    # throughput: which day did work get created / accepted
    start = (now - timedelta(days=max(1, days) - 1)).date()
    by_day: dict[str, dict[str, int]] = {
        (start + timedelta(days=i)).isoformat(): {"created": 0, "accepted": 0}
        for i in range(max(1, days))
    }
    for task in tasks:
        created = _ts(task.created_at)
        if created and created.date().isoformat() in by_day:
            by_day[created.date().isoformat()]["created"] += 1
        accepted = _accepted_at(task)
        if accepted and accepted.date().isoformat() in by_day:
            by_day[accepted.date().isoformat()]["accepted"] += 1
    throughput = [{"date": day, **counts} for day, counts in sorted(by_day.items())]

    per_role = {}
    for role in cfg.roles:
        mine = [t for t in tasks if t.role == role]
        mine_leads = [v for v in (lead_time_s(t) for t in mine) if v is not None]
        per_role[role] = {
            "tasks": len(mine),
            "accepted": sum(1 for t in mine if t.status == STATUS_ACCEPTED),
            "failed": sum(1 for t in mine if t.status == STATUS_FAILED),
            "open": sum(1 for t in mine if t.status not in (STATUS_ACCEPTED, STATUS_FAILED)),
            "overdue": sum(1 for t in mine if t.overdue),
            "nudges": sum(int(t.nudges or 0) for t in mine),
            "restatements": sum(1 for t in mine if t.restatement),
            "avg_lead_s": (sum(mine_leads) / len(mine_leads)) if mine_leads else None,
            "skills": skills.get(role, 0),
        }

    assignments = sum(len(t.assignments or []) for t in tasks)
    adapters: dict[str, int] = {}
    for task in tasks:
        for item in task.assignments or []:
            name = str(item.get("adapter", "?"))
            adapters[name] = adapters.get(name, 0) + 1

    return {
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "window_days": max(1, days),
        "totals": {
            "tasks": len(tasks),
            "open": sum(1 for t in tasks if t.status not in (STATUS_ACCEPTED, STATUS_FAILED)),
            **verdicts,
        },
        "rates": {
            "verified": verified_rate,
            "rework": (sum(1 for t in tasks if (t.round or 0) > 0) / len(tasks)) if tasks else None,
            "restated": (sum(1 for t in tasks if t.restatement) / len(tasks)) if tasks else None,
        },
        "lead_time": lead,
        "throughput": throughput,
        "roles": per_role,
        "collaboration": {**_conversation_stats(cfg), **_huddle_stats(cfg)},
        "supervision": {
            "overdue_now": verdicts["overdue"],
            "nudges": sum(int(t.nudges or 0) for t in tasks),
            "nudged_tasks": sum(1 for t in tasks if int(t.nudges or 0) > 0),
        },
        "engine": {"assignments": assignments, "by_adapter": adapters},
    }


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.0f}%"


def _dur(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def render_text(report: dict[str, Any]) -> str:
    """Human view of `build_report`: the same numbers the panel shows."""
    totals = report["totals"]
    rates = report["rates"]
    lead = report["lead_time"]
    engine = report["engine"]
    collab = report["collaboration"]
    supervision = report["supervision"]

    out = [
        f"window: last {report['window_days']} days · generated {report['generated_at']}",
        f"tasks: {totals['tasks']}  open {totals['open']}  accepted {totals['accepted']}  "
        f"rework {totals['rework']}  failed {totals['failed']}  restating {totals['restating']}",
        f"verified rate: {_pct(rates['verified'])}   rework rate: {_pct(rates['rework'])}   "
        f"restated: {_pct(rates['restated'])}",
        f"lead time: p50 {_dur(lead['p50_s'])}  p90 {_dur(lead['p90_s'])}  "
        f"avg {_dur(lead['avg_s'])}  (n={lead['count']})",
        f"supervision: overdue {supervision['overdue_now']}  nudges {supervision['nudges']} "
        f"on {supervision['nudged_tasks']} task(s)",
        f"collaboration: {collab['conversations']} conversation(s), {collab['messages']} message(s), "
        f"{collab['undelivered']} undelivered · {collab['huddles']} huddle(s), {collab['turns']} turn(s)",
        f"engine: {engine['assignments']} adapter run(s)"
        + (f" — {', '.join(f'{k}×{v}' for k, v in sorted(engine['by_adapter'].items()))}" if engine["by_adapter"] else ""),
    ]

    role_rows = report["roles"]
    if role_rows:
        width = max(len(name) for name in role_rows)
        out.append("")
        out.append(f"{'role':<{width}}  tasks  acc  fail  open  late  nudges  skills  avg lead")
        for name, row in role_rows.items():
            out.append(
                f"{name:<{width}}  {row['tasks']:>5}  {row['accepted']:>3}  {row['failed']:>4}  "
                f"{row['open']:>4}  {row['overdue']:>4}  {row['nudges']:>6}  {row['skills']:>6}  "
                f"{_dur(row['avg_lead_s'])}"
            )

    days = [d for d in report["throughput"] if d["created"] or d["accepted"]]
    if days:
        peak = max(max(d["created"], d["accepted"]) for d in days) or 1
        out.append("")
        out.append("throughput (created / accepted per day)")
        for day in days[-14:]:
            bar = "█" * max(1, round(10 * day["created"] / peak))
            acc = "▓" * max(0, round(10 * day["accepted"] / peak))
            out.append(f"  {day['date'][5:]}  {day['created']:>2}  {day['accepted']:>2}  {bar}{acc}")
    return "\n".join(out)
