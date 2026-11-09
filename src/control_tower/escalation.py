"""Escalation: a blocked/failed job is handed to the issues bot, not a human.

John, 2026-11-09: a blocked task must not sit in 「需要你」 hoping a person
notices. The tower auto-delivers it to a dedicated issues role which dedupes,
classifies, routes (tower-bug → maintainer session / exec → role / infra →
workaround + planner / policy → deputy → planner) and writes the origin task
back (tower attention claim|resolve). Everything is ledgered; delivery is
idempotent per task.
"""

from __future__ import annotations

from typing import Any

from .board import Board, Task, attention_status, now_ts
from .config import TowerConfig


def _cfg_escalation(cfg: TowerConfig) -> tuple[bool, str, int]:
    return (
        bool(getattr(cfg, "escalation_enabled", True)),
        str(getattr(cfg, "escalation_role", "") or ""),
        max(1, int(getattr(cfg, "escalation_max_attempts", 1))),
    )


def _history_tail(task: Task, n: int = 6) -> str:
    rows = []
    for h in (task.history or [])[-n:]:
        rows.append(f"- {h.get('ts', '')} {h.get('status', '')} "
                    f"{str(h.get('note', ''))[:160]}")
    return "\n".join(rows) or "- (no history)"


def _report_digest(task: Task) -> str:
    report = task.report or {}
    summary = str(report.get("summary") or "").strip()
    files = report.get("files") or []
    checks = report.get("checks") or []
    if not summary:
        return "(no report)"
    return (f"{summary[:400]}\n"
            f"- files: {len(files)} · checks: {len(checks)}")


def build_brief(task: Task, question: str, note: str = "") -> str:
    """The ticket the issues bot receives — everything it needs to route."""
    return f"""[问题工单] 收口 {task.id}（role={task.role}）

问题：{question or "blocked/failed，需要收口"}

来源任务：{task.id}
- 状态：status={task.status} state={task.state or '-'}
- 轮次：{task.round}/{task.max_rounds}
- 最近报告：{_report_digest(task)}
- 附加说明：{note or "(none)"}
- 最近历史：
{_history_tail(task)}

你的职责（见 roles/issues/AGENT.md / docs/issues-flow.md）：
1) 去重定级：kind ∈ tower-bug | exec | infra | policy；severity ∈ Critical | High | Medium | Low
2) 分类路由：
   - tower-bug → 维护者会话 session-4323654f-59d8-4be8-8ef0-443aa9182587
   - exec       → 对应角色重派（tower rework 或新 dispatch）
   - infra      → 记录事实/workaround，报总控 planner
   - policy     → 项目秘书 deputy → 总控 planner
3) 回写原任务（必做）：tower attention claim {task.id} --by {task.role and "issues"}，
   处置后 tower attention resolve {task.id} --as dropped|rework|accepted --note "<原因>"
4) 回执：roles/issues/{task.id}-REPORT.md（Summary 一行：ISSUE=… KIND=… SEVERITY=… ROUTE=… STATUS=…）
5) 账：追加一行到 .tower/issues/ledger.jsonl

判断标准：找 John = 价格/删页/结构改版/对外账号/人肉确认；不找 John = TDK 批改、
quick-wins、内容小改、任务收尾、看板清理、代理/工具修复、额度类外部限制（记录+次日重试）。
"""


def already_delivered(task: Task, role: str, max_attempts: int) -> bool:
    escalation = task.escalation or {}
    return (bool(escalation.get("delivered_at"))
            and escalation.get("role") == role
            and int(escalation.get("attempts", 0)) >= max_attempts)


def _open_escalation_alive(board: Board, task: Task) -> bool:
    """One open escalation per task; a closed issues task may be escalated again."""
    escalation = task.escalation or {}
    issue_id = escalation.get("issues_task") or ""
    if not issue_id or not escalation.get("open"):
        return False
    try:
        issue_task = board.load(str(issue_id))
    except KeyError:
        return False
    return issue_task.status != "accepted"


def deliver(cfg: TowerConfig, board: Board, task: Task, target_role: str,
            question: str = "", note: str = "", chain: bool = False) -> dict[str, Any]:
    """Create the target role's task, record it on the origin, enqueue the run.

    `chain=True` appends to escalation.chain (issues → deputy → planner);
    the automatic delivery keeps a single attempt per task (max_attempts).
    """
    if target_role not in cfg.roles:
        return {"ok": False, "reason": f"unknown role {target_role!r}"}
    brief = build_brief(task, question, note)
    issue_task = board.create(
        target_role, brief, batch="issues",
        mandate={"source_task": task.id, "strength": "inferred",
                 "kind_hint": str((task.escalation or {}).get("kind_hint", ""))},
    )
    escalation = dict(task.escalation or {})
    attempts = int(escalation.get("attempts", 0))
    previous_chain = list(escalation.get("chain") or [])
    escalation.update({
        "delivered_at": now_ts(),
        "role": target_role,
        "issues_task": issue_task.id,
        "attempts": attempts + 1,
        "open": True,
        "source_state": task.state,
        "source_status": task.status,
        "question": (question or "")[:300],
    })
    if chain:
        escalation["chain"] = previous_chain + [target_role]
    task.escalation = escalation
    board.save(task)
    board.event("escalation_delivered", task=task.id, to=target_role,
                issues_task=issue_task.id)
    board.log("escalation_delivered", task=task.id, role=task.role,
              to=target_role, issues_task=issue_task.id)

    from .worker import enqueue, ensure_worker

    enqueue(cfg, "dispatch", issue_task.id)
    try:
        ensure_worker(cfg)
    except OSError:
        pass
    return {"ok": True, "issues_task": issue_task.id, "role": target_role}


def maybe_escalate(cfg: TowerConfig, board: Board, task: Task,
                   question: str = "", note: str = "") -> dict[str, Any] | None:
    """Auto-deliver a blocked/failed task to the issues bot (idempotent)."""
    enabled, role, max_attempts = _cfg_escalation(cfg)
    if not enabled or not role:
        return None
    if role not in cfg.roles or task.role == role:
        return None
    if attention_status(task) == "resolved":
        board.log("escalation_suppressed_resolved", task=task.id)
        return None
    if already_delivered(task, role, max_attempts):
        board.log("escalation_already_delivered", task=task.id, role=role)
        return None
    if _open_escalation_alive(board, task):
        return None
    result = deliver(cfg, board, task, role, question=question, note=note)
    return result if result.get("ok") else None
