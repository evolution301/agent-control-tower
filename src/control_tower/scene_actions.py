"""The scene's buttons, executed against real tasks.

This is the one place the board may write, and only these four verbs. Each one
ends in the ledger, so "who approved this" stays answerable.
"""

from __future__ import annotations

import threading
from typing import Any

from .board import Board, STATUS_ACCEPTED, STATUS_FAILED, STATUS_REWORK, Task
from .config import TowerConfig
from .triage import triage_task
from .verify import verify_report

ACTIONS = ("approve", "reply", "skip", "repair")


def _dispatch(cfg: TowerConfig, task_id: str, note: str = "") -> None:
    """Re-dispatch in a daemon thread: a real bot turn must not block an HTTP reply."""
    from .dispatch import dispatch

    def run() -> None:
        board = Board(cfg)
        task = board.load(task_id)
        if note:
            task.brief = (task.brief + "\n\n## Rework: a person replied\n" + note)[:8000]
            board.save(task)
        dispatch(cfg, board, task)

    threading.Thread(target=run, daemon=True).start()


def _find(board: Board, task_id: str) -> Task:
    return board.load(task_id)


def perform(cfg: TowerConfig, board: Board, action: str, task_id: str,
            note: str = "") -> dict[str, Any]:
    """Run one control-room action. Raises ValueError for a refusal (HTTP 409)."""
    if action not in ACTIONS:
        raise KeyError(f"unknown action {action!r}; use {', '.join(ACTIONS)}")
    task = _find(board, task_id)

    if action == "approve":
        verdict = verify_report(cfg, task)
        if not verdict.ok:
            raise ValueError("cannot approve: " + "; ".join(verdict.problems[:3]))
        board.transition(task, STATUS_ACCEPTED, note="approved in the control room",
                         verification=verdict.as_dict())
        board.log("scene_approved", task=task.id, role=task.role)
        return {"ok": True, "task": task.id, "outcome": "accepted"}

    if action == "reply":
        if not note.strip():
            raise ValueError("a reply needs text")
        board.transition(task, STATUS_REWORK,
                         note=f"a person replied: {note[:120]}")
        task.round += 1
        task.attention = None
        task.state = ""
        board.save(task)
        board.log("scene_replied", task=task.id, role=task.role, chars=len(note))
        _dispatch(cfg, task.id, note)
        return {"ok": True, "task": task.id, "outcome": "redispatched"}

    if action == "skip":
        task.attention = None
        if task.state == "blocked":
            task.state = ""
        board.save(task)
        board.log("scene_skipped", task=task.id, role=task.role)
        return {"ok": True, "task": task.id, "outcome": "dismissed"}

    # repair: the tower's own retry path, in the background when it dispatches
    result = triage_task(cfg, board, task,
                         dispatch_fn=lambda _b, t, reasons: _dispatch(cfg, t.id, reasons))
    board.log("scene_repair", task=task.id, role=task.role, outcome=result.get("outcome", ""))
    return {"ok": True, "task": task.id, "outcome": result.get("outcome", "retried")}


def _least_busy(board: Board, cfg: TowerConfig) -> str:
    """The role with the fewest open tasks gets the question."""
    open_counts: dict[str, int] = {role: 0 for role in cfg.roles}
    for task in board.all():
        if task.status != STATUS_ACCEPTED and task.role in open_counts:
            open_counts[task.role] += 1
    return min(open_counts, key=open_counts.get) if open_counts else ""


def ask(cfg: TowerConfig, board: Board, text: str) -> dict[str, Any]:
    """Free-text chat: forward the question to a real agent as a real task.

    The model on the other end is whatever adapter the role runs; the panel polls
    `/api/task` for the report. This is the honest path — the tower never fakes a
    model reply.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("an empty question cannot be forwarded")
    role = _least_busy(board, cfg)
    if not role:
        raise ValueError("this tower has no roles to ask")
    task = board.create(role, f"[control room] {text}"[:4000])
    board.save(task)
    board.log("scene_ask", task=task.id, role=role, chars=len(text))
    _dispatch(cfg, task.id)
    return {"ok": True, "task": task.id, "role": role, "outcome": "asked"}
