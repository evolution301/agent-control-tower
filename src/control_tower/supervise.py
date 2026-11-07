"""Deadline supervision: every job has a due time, and silence gets chased.

A dispatch alone is a request, not a job: nobody follows it up. This module closes
that hole the way a person would manage a team - the task carries a due time, and
when it passes without a report the lead pulls the role up, in the role's own
session, and writes down the answer.

Three outcomes are all recorded, and they are different:

* the role answers with a real report (Files + Checks) -> it is filed and verified
  like any other report, so a late job can still be accepted;
* the role answers with a status but no report -> the task stays open, marked
  overdue, with the answer attached, so the lead can see *why* it is late;
* the adapter is unreachable or silent -> the nudge count goes up and the task
  stays overdue. Repeated silence is the lead's signal to reassign the work.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from .adapters import AdapterRequest, get_adapter
from .board import Board, OPEN_STATES, STATUS_REPORTED, Task, now_ts
from .config import TowerConfig
from .dispatch import parse_report

TS_FMT = "%Y-%m-%dT%H:%M:%S"
DUE_RE = re.compile(r"^(\d+)\s*([smhd])$", re.IGNORECASE)
UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_HUMAN_FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", TS_FMT, "%Y-%m-%d")

SUPERVISE_CONTRACT = """## Deadline check (answer the lead, do not restart the work)

You are the **{role}** role. Task {task_id} was due at {due} and no report has
reached the tower. The lead is asking where it stands — not for a rewrite of the
deliverable, and not for a promise about the future.

Answer with exactly these sections:

    ## Status
    <progress|ready|blocked|failed|waiting>

    ## Summary
    <what is actually done right now, 2-5 sentences>

    ## Files
    - <path> — <what exists right now, or "none">

    ## Checks
    - <a command you actually ran> → <the result you actually saw, or "none">

    ## Attention
    <only when blocked or failed: the one concrete question that unblocks you>

If the work is in fact finished, fill Files and Checks with real ones: the tower
verifies them exactly like any other report.
"""


# --------------------------------------------------------------------- due times


def parse_due(spec: str, now: datetime | None = None) -> str:
    """Read `90m`, `2h`, `3d`, `2026-11-09 18:00` or ISO; return a stamped ISO time."""
    text = (spec or "").strip()
    if not text:
        return ""
    match = DUE_RE.match(text)
    if match:
        base = now or datetime.now()
        return (base + timedelta(seconds=int(match.group(1)) * UNITS[match.group(2).lower()])).strftime(TS_FMT)
    for fmt in _HUMAN_FORMATS:
        try:
            return datetime.strptime(text, fmt).strftime(TS_FMT)
        except ValueError:
            continue
    raise ValueError(
        f"cannot read due time {spec!r} — use 90m / 2h / 3d, or '2026-11-09 18:00'"
    )


def _as_dt(text: str) -> datetime | None:
    for fmt in (TS_FMT, "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime((text or "").strip(), fmt)
        except ValueError:
            continue
    return None


def is_overdue(task: Task, now: datetime | None = None) -> bool:
    due = _as_dt(task.due_at)
    if due is None:
        return False
    return (now or datetime.now()) > due


def due_human(task: Task, now: datetime | None = None) -> str:
    """`due in 40m` / `overdue 2h` / `overdue 3d`, or '' when there is no due time."""
    due = _as_dt(task.due_at)
    if due is None:
        return ""
    delta = int((due - (now or datetime.now())).total_seconds())
    if delta >= 0:
        return f"due in {_span(delta)}"
    return f"overdue {_span(-delta)}"


def _span(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


# ------------------------------------------------------------------- supervise


def render_supervise_prompt(cfg: TowerConfig, task: Task) -> str:
    due = _as_dt(task.due_at)
    return SUPERVISE_CONTRACT.format(
        role=task.role,
        task_id=task.id,
        due=due.strftime("%Y-%m-%d %H:%M") if due else "an earlier time",
    )


def overdue_tasks(board: Board, cfg: TowerConfig | None = None, now: datetime | None = None) -> list[Task]:
    """Open tasks whose due time has passed."""
    out = []
    for task in board.all():
        if task.status in OPEN_STATES and task.status != "accepted" and is_overdue(task, now):
            out.append(task)
    return out


def supervise(cfg: TowerConfig, board: Board, now: datetime | None = None) -> dict:
    """Pull up every open task that is past due. Returns what happened, per task."""
    results: list[dict] = []
    for task in overdue_tasks(board, cfg, now):
        role = cfg.roles.get(task.role)
        if role is None:  # a role removed while its task was open
            board.log("supervise_skipped", task=task.id, reason=f"unknown role {task.role!r}")
            continue
        prompt = render_supervise_prompt(cfg, task)
        task_dir = cfg.task_dir(task.id)
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / f"supervise-{task.nudges + 1}.md").write_text(prompt, encoding="utf-8")

        options = dict(cfg.adapter_options.get(role.adapter) or {})
        adapter = get_adapter(role.adapter, options)
        board.log("supervise_nudge", task=task.id, role=task.role, adapter=adapter.name,
                  nudge=task.nudges + 1, due_at=task.due_at)
        req = AdapterRequest(
            role=task.role,
            prompt=prompt,
            cwd=cfg.role_dir(task.role),
            timeout_s=role.timeout_s,
            env=role.env,
            task_id=task.id,
            options=options,
        )
        result = adapter.run(req)
        (task_dir / f"supervise-{task.nudges + 1}-answer.txt").write_text(result.text, encoding="utf-8")
        board.add_assignment(task, adapter.name, result.exit_code)
        task.nudges += 1
        task.overdue = True

        if result.exit_code != 0:
            board.transition(task, task.status, note=f"supervise: no answer (exit {result.exit_code})")
            board.log("supervise_unreachable", task=task.id, exit_code=result.exit_code)
            results.append({"task": task.id, "role": task.role, "outcome": "unreachable",
                            "nudges": task.nudges, "detail": result.text[:300]})
            continue

        report = parse_report(result.text)
        # "- none" is a placeholder, not a file list: a status-only answer must
        # read as still open, never as a recovered report.
        real_files = [f for f in report.get("files", []) if f.strip().lower() not in ("none", "n/a", "-")]
        real_checks = [c for c in report.get("checks", []) if c.strip().lower() not in ("none", "n/a", "-")]
        recovered = bool(real_files) and bool(real_checks)
        if recovered:
            task.report = report
            task.state = report.get("state", "")
            task.attention = report.get("attention")
            task.overdue = False
            board.transition(task, STATUS_REPORTED, note="late report recovered by supervise", report=report)
            board.push_report(task, report)
            board.log("supervise_report_recovered", task=task.id, role=task.role)
            results.append({"task": task.id, "role": task.role, "outcome": "report_recovered",
                            "nudges": task.nudges, "summary": report.get("summary", "")})
            continue

        task.state = report.get("state", "") or task.state
        task.attention = report.get("attention") or task.attention
        board.transition(task, task.status, note=f"supervise: still open — {report.get('state') or 'no status'}")
        board.log("supervise_still_open", task=task.id, role=task.role,
                  state=report.get("state", ""), nudges=task.nudges)
        results.append({"task": task.id, "role": task.role, "outcome": "still_open",
                        "nudges": task.nudges, "state": report.get("state", ""),
                        "summary": report.get("summary", "")})

    return {"checked_at": now_ts(), "touched": len(results), "results": results}
