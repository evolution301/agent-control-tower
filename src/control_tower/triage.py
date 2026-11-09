"""Triage: the tower does the follow-up work itself.

The rule the user actually wants: **a bot team must not need a person for ordinary
follow-through.** Verifying a delivered report, sending a failed job back with its
own reasons attached, retrying inside the round budget, recovering a late report —
all of that is the tower's job. A human is asked only when the tower has run out of
moves: a role that is blocked on a decision, or a job that failed every round.

So `triage` walks the queue and does, for each task:

* **reported** → run `verify_report`. Pass: accept (`auto_accepted` in the ledger).
  Fail: send it back *quoting the verification problems*, so the role learns from
  its own failure (a rework round). Out of rounds: escalate to a person.
* **failed** (adapter died) → retry while rounds remain; otherwise escalate.
* **restating** → left to the lead's judgement: confirming understanding is a
  judgement call, not a check (`tower restate ok`).

Every decision lands in the ledger, so "why did nobody ask me" is answerable.
"""

from __future__ import annotations

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
from .verify import verify_report


def _escalate(cfg: TowerConfig, board: Board, task: Task, question: str) -> None:
    """Hand a task to the issues bot (v0.12), a person only if no issues role.

    A resolved attention is never re-raised: the issue was closed on purpose, so
    a late triage pass records the fact instead of flipping the board back to
    「需要你」 (John, 2026-11-09).
    """
    from .board import attention_status

    if attention_status(task) == "resolved":
        board.log("triage_escalation_suppressed", task=task.id,
                  reason="attention already resolved")
        return
    task.attention = {"question": question, "status": "open"}
    task.state = "blocked"
    board.transition(task, task.status, note=f"escalated: {question[:160]}")
    board.log("triage_escalated", task=task.id, role=task.role, question=question[:300])
    from .escalation import maybe_escalate

    delivered = maybe_escalate(cfg, board, task, question=question)
    if delivered:
        board.log("triage_handed_to_issues", task=task.id,
                  issues_task=delivered.get("issues_task", ""))


def redispatch_with_reasons(cfg: TowerConfig, board: Board, task: Task, reasons: str) -> None:
    """Re-dispatch a rejected job with its own failure reasons attached.

    The one shared re-dispatch path for sync (`cli._triage_dispatch`) and the
    async worker, so a rework reads the same no matter who ran the triage.
    """
    task.brief = (task.brief + "\n\n## Rework: the previous attempt failed verification\n"
                  + reasons)[:8000]
    board.save(task)
    from .dispatch import dispatch

    dispatch(cfg, board, task)


def triage_task(cfg: TowerConfig, board: Board, task: Task, dispatch_fn=None) -> dict[str, Any]:
    """Move one task forward without a human. Returns what happened.

    Contract for `dispatch_fn` (the re-dispatch callback): it is called as
    ``dispatch_fn(board, task, reasons)`` — **exactly three positional args, no
    cfg**. The tower owns the verify/retry decision; the callback closes over its
    own config (see ``cli._auto_triage``). A 4-arg callable here raises TypeError
    mid-dispatch, which is exactly the crash this contract wording prevents.
    """
    if task.status in (STATUS_ACCEPTED,):
        return {"task": task.id, "outcome": "done"}
    if task.status == STATUS_RESTATING:
        return {"task": task.id, "outcome": "awaiting_restatement_ok"}

    # an adversarial review in flight owns the task: nobody else may act on it
    if (task.review or {}).get("verdict") == "pending":
        return {"task": task.id, "outcome": "awaiting_review"}

    # a resolved attention closed the issue on purpose: a late report must not
    # reopen the board's 「需要你」 queue (v0.12, John 2026-11-09)
    if (task.attention or {}).get("status") == "resolved":
        return {"task": task.id, "outcome": "resolved_hold"}

    def _pick_reviewer() -> str:
        """First configured reviewer that exists here; the author never reviews itself."""
        for name in getattr(cfg, "review_roles", []) or []:
            if name in cfg.roles and name != task.role:
                return name
        return ""

    if task.status == STATUS_REPORTED:
        verdict = verify_report(cfg, task)
        if verdict.ok:
            reviewer = _pick_reviewer()
            rounds = int((task.review or {}).get("rounds", 0))
            if reviewer and rounds < int(getattr(cfg, "review_budget", 1)):
                # pstack thermos/interrogate: an independent reviewer gets a
                # "break it, prove by run" pass before the tower accepts.
                task.review = {"rounds": rounds, "verdict": "pending",
                               "reviewer": reviewer, "findings": 0, "proved": 0}
                board.save(task)
                from .worker import enqueue

                enqueue(cfg, "adversarial", task.id, payload={"reviewer": reviewer})
                board.event("review_queued", task=task.id, reviewer=reviewer)
                board.log("review_queued", task=task.id, reviewer=reviewer)
                return {"task": task.id, "outcome": "review_queued"}
            board.transition(task, STATUS_ACCEPTED, note="auto-accepted: verification passed",
                             verification=verdict.as_dict())
            board.log("auto_accepted", task=task.id, role=task.role)
            return {"task": task.id, "outcome": "accepted"}

        reasons = "; ".join(verdict.problems)[:400]
        remaining = int(task.max_rounds or 0) - int(task.round or 0)
        if remaining > 0:
            board.transition(task, STATUS_REWORK, note=f"auto-rework: {reasons[:200]}")
            task.round += 1
            task.attention = None
            task.state = ""
            board.save(task)
            board.log("auto_rework", task=task.id, role=task.role, round=task.round,
                      reasons=reasons[:500])
            if dispatch_fn is not None:
                dispatch_fn(board, task, reasons)
            return {"task": task.id, "outcome": "reworked", "round": task.round,
                    "reasons": reasons}
        _escalate(cfg, board, task, f"{task.id} failed verification after {task.round} round(s): "
                               f"{verdict.problems[0] if verdict.problems else 'see report'}")
        return {"task": task.id, "outcome": "escalated", "reasons": reasons}

    if task.status == STATUS_FAILED:
        remaining = int(task.max_rounds or 0) - int(task.round or 0)
        output = (task.history[-1].get("output", "") if task.history else "") or "adapter failed"
        if remaining > 0:
            board.transition(task, STATUS_REWORK, note="auto-retry: adapter failed")
            task.round += 1
            task.state = ""
            task.attention = None
            board.save(task)
            board.log("auto_retry", task=task.id, role=task.role, round=task.round)
            if dispatch_fn is not None:
                dispatch_fn(board, task, str(output)[:400])
            return {"task": task.id, "outcome": "retried", "round": task.round}
        _escalate(cfg, board, task, f"{task.id} keeps failing after {task.round} round(s): "
                               f"{str(output)[:160]}")
        return {"task": task.id, "outcome": "escalated"}

    return {"task": task.id, "outcome": "waiting"}


def triage(cfg: TowerConfig, board: Board | None = None, dispatch_fn=None) -> dict[str, Any]:
    """Work every queue item the tower can move on its own.

    `dispatch_fn` contract: called as ``dispatch_fn(board, task, reasons)`` — three
    positional args, no cfg (see `triage_task`).
    """
    board = board or Board(cfg)
    results = [triage_task(cfg, board, task, dispatch_fn=dispatch_fn)
               for task in board.needs_lead()]
    counts: dict[str, int] = {}
    for item in results:
        counts[item["outcome"]] = counts.get(item["outcome"], 0) + 1
    return {
        "touched": len(results),
        "outcomes": counts,
        "results": results,
        "needs_person": [t.id for t in board.needs_person()],
    }
