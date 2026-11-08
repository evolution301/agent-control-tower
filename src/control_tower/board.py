"""Append-only ledger + task records.

Every state change is a ledger event (JSONL). The per-task JSON file is a
materialised view that is cheap to read and safe to commit; the ledger is the
source of truth for "who claimed what, when, and what did they claim".
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

STATUS_PENDING = "pending"
STATUS_CLAIMED = "claimed"
STATUS_IN_PROGRESS = "in_progress"
STATUS_REPORTED = "reported"
STATUS_ACCEPTED = "accepted"
STATUS_REWORK = "rework"
STATUS_FAILED = "failed"
# v0.8 — point 7 of the AI-employee playbook: a task can sit between "created"
# and "work started" while a person confirms the role understood the brief.
# Nothing but a restatement has run in this state, so no report can exist yet.
STATUS_RESTATING = "restating"

OPEN_STATES = {
    STATUS_PENDING,
    STATUS_CLAIMED,
    STATUS_IN_PROGRESS,
    STATUS_REPORTED,
    STATUS_REWORK,
    STATUS_RESTATING,
}

# Report states borrowed from dsh-superagent's domain model: a role says what it is
# waiting for, in a closed vocabulary, instead of burying it in prose. "person" states
# are what the human must look at; everything else can stay quiet.
REPORT_STATES = {
    "progress": "working",        # still going, nothing needed
    "waiting": "idle",            # turn finished, waiting for a trigger
    "ready": "lead/review",       # deliverable is ready — the tower verifies it
    "blocked": "person/blocked",  # cannot continue without a human decision
    "failed": "person/failed",    # gave up; needs a human
    "dropped": "idle",            # nothing to do
}
# Only these two belong to a human. Everything else is work the tower itself can
# move: verifying a report, confirming a restatement, retrying a rework. Counting
# `ready` here was what made "needs you" show 32 items when 30 of them were
# already accepted — the human queue must stay honest or it stops being read.
PERSON_STATES = {"blocked", "failed"}
# States the lead (the tower, or an agent driving it) is expected to act on.
LEAD_STATES = {"ready", "restating", "rework", "progress", "waiting"}


def now_ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


@dataclass
class Task:
    id: str
    role: str
    brief: str
    batch: str = ""
    status: str = STATUS_PENDING
    created_at: str = field(default_factory=now_ts)
    updated_at: str = field(default_factory=now_ts)
    round: int = 0
    max_rounds: int = 1
    write_scopes: list[str] = field(default_factory=list)
    report: dict[str, Any] | None = None
    history: list[dict[str, Any]] = field(default_factory=list)
    # --- v0.2: dsh-superagent-style structured facts ---
    state: str = ""                      # report state (REPORT_STATES key)
    attention: dict[str, Any] | None = None   # {"question": ..., "options": [...]} when a person must act
    mandate: dict[str, Any] = field(default_factory=dict)  # why/how far the work may go
    assignments: list[dict[str, Any]] = field(default_factory=list)  # one per adapter run
    idem_key: str = ""
    # v0.8 — the restatement gate: what the role said it understood, and whether
    # a person confirmed it. Work is only dispatched once restate_ok is true.
    restatement: str = ""
    restate_ok: bool = False
    restated_at: str = ""
    # v0.8 — supervision: a task is a job with a due date, and a role that misses
    # it gets pulled up by the lead instead of quietly rotting.
    due_at: str = ""
    overdue: bool = False
    nudges: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Task":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


class Board:
    """File-backed task board plus ledger."""

    def __init__(self, cfg):
        self.cfg = cfg

    # -- ledger -----------------------------------------------------------
    def log(self, event: str, **payload: Any) -> dict[str, Any]:
        self.cfg.state_dir.mkdir(parents=True, exist_ok=True)
        record = {"ts": now_ts(), "event": event, **payload}
        with self.cfg.ledger_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    # -- tasks ------------------------------------------------------------
    def path_for(self, task_id: str) -> Path:
        return self.cfg.tasks_dir / f"{task_id}.json"

    def save(self, task: Task) -> Task:
        task.updated_at = now_ts()
        path = self.path_for(task.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(task.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        return task

    def load(self, task_id: str) -> Task:
        path = self.path_for(task_id)
        if not path.exists():
            raise KeyError(f"unknown task {task_id}")
        return Task.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def all(self) -> list[Task]:
        if not self.cfg.tasks_dir.exists():
            return []
        tasks = []
        for p in sorted(self.cfg.tasks_dir.glob("T-*.json")):
            try:
                tasks.append(Task.from_dict(json.loads(p.read_text(encoding="utf-8"))))
            except json.JSONDecodeError:
                continue
        return sorted(tasks, key=lambda t: t.id)

    def by_role(self, role: str) -> list[Task]:
        return [t for t in self.all() if t.role == role]

    def open_tasks(self) -> list[Task]:
        return [t for t in self.all() if t.status in OPEN_STATES]

    def find_by_idem(self, idem_key: str) -> Task | None:
        """Idempotence: the same request key never creates a second task."""
        if not idem_key:
            return None
        for t in self.all():
            if t.idem_key == idem_key:
                return t
        return None

    def create(self, role: str, brief: str, batch: str = "", write_scopes: list[str] | None = None,
               idem_key: str = "", mandate: dict[str, Any] | None = None) -> Task:
        cfg_role = self.cfg.roles.get(role)
        if cfg_role is None:
            raise KeyError(f"unknown role {role!r}; known roles: {', '.join(sorted(self.cfg.roles))}")
        from .config import next_task_id

        task = Task(
            id=next_task_id(self.cfg),
            role=role,
            brief=brief,
            batch=batch,
            write_scopes=list(write_scopes if write_scopes is not None else cfg_role.write_scopes),
            max_rounds=cfg_role.max_rounds,
            idem_key=idem_key,
            mandate=dict(mandate or {}),
        )
        self.save(task)
        self.log("task_created", task=task.id, role=role, batch=batch, brief=brief[:500],
                 idem_key=idem_key, mandate=task.mandate)
        return task

    def add_assignment(self, task: Task, adapter: str, exit_code: int) -> Task:
        """Record one executor run: who ran, when, and how it ended.

        Mirrors the Assignment concept: the work item outlives the executor that
        happened to run it, so hand-offs and retries stay auditable.
        """
        task.assignments.append({"ts": now_ts(), "adapter": adapter, "exit_code": exit_code})
        self.save(task)
        self.log("assignment", task=task.id, adapter=adapter, exit_code=exit_code)
        return task

    def needs_person(self) -> list[Task]:
        """Tasks that genuinely require a human decision.

        A task is *not* here just because it failed: an adapter failure with
        retries left belongs to the tower (`needs_lead`). A task arrives here when
        its state is `blocked` or `failed` — which only triage escalation sets once
        the retry budget is spent (docs/board-model.md: blocked/failed 且重试用尽).
        """
        out = [t for t in self.all() if t.status != STATUS_ACCEPTED and t.state in PERSON_STATES]
        return sorted(out, key=lambda t: t.updated_at, reverse=True)

    def needs_lead(self) -> list[Task]:
        """Work the tower can still move on its own: verify, retry, confirm.

        A task that is *currently running* is not queue work: the bot owns it and
        nobody — person or lead — should be told to act on it. An adapter-failed
        task with retry budget left is here too: `triage` retries it and only
        escalates to a person once the budget is spent.
        """
        person_ids = {t.id for t in self.needs_person()}
        idle_hands = {STATUS_CLAIMED, STATUS_IN_PROGRESS, STATUS_PENDING}
        out = [
            t for t in self.all()
            if t.status != STATUS_ACCEPTED
            and t.status not in idle_hands
            and t.id not in person_ids
            and (t.status in (STATUS_REPORTED, STATUS_REWORK, STATUS_RESTATING, STATUS_FAILED)
                 or t.state in ("ready", "waiting")
                 or t.overdue)
        ]
        return sorted(out, key=lambda t: t.updated_at, reverse=True)

    def transition(self, task: Task, status: str, note: str = "", **extra: Any) -> Task:
        task.status = status
        task.history.append({"ts": now_ts(), "status": status, "note": note, **extra})
        self.save(task)
        self.log("task_status", task=task.id, role=task.role, status=status, note=note[:500])
        return task

    # -- inbox ------------------------------------------------------------
    def push_report(self, task: Task, report: dict[str, Any]) -> None:
        self.cfg.state_dir.mkdir(parents=True, exist_ok=True)
        entry = {"ts": now_ts(), "task": task.id, "role": task.role, "report": report, "read": False}
        with self.cfg.inbox_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def inbox(self, unread_only: bool = False) -> list[dict[str, Any]]:
        if not self.cfg.inbox_path.exists():
            return []
        items = []
        for line in self.cfg.inbox_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if unread_only and entry.get("read"):
                continue
            items.append(entry)
        return items

    def mark_inbox_read(self) -> int:
        items = self.inbox()
        if not items:
            return 0
        for item in items:
            item["read"] = True
        text = "".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items)
        self.cfg.inbox_path.write_text(text, encoding="utf-8")
        return len(items)
