"""Prompt rendering and dispatch.

The tower's leverage comes from a strict contract in every prompt:

  * you own exactly these files
  * you write your deliverables + a REPORT file
  * you never claim a check you did not run

and from the fact that the lead never *accepts* a report without verifying it.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import groups, kickoff
from .adapters import AdapterRequest, get_adapter
from .board import (
    Board,
    Task,
    STATUS_ACCEPTED,
    STATUS_CLAIMED,
    STATUS_IN_PROGRESS,
    STATUS_REPORTED,
    STATUS_FAILED,
    STATUS_RESTATING,
    now_ts,
)
from .config import TowerConfig, next_task_id

CONTRACT = """## Contract (read this first)

You are the **{role}** role inside a control-tower workflow.

1. Workspace: `{workspace}`. Your own folder: `{role_dir}`.
2. Write scope: {scopes}
   Put deliverables there. Do not edit files outside your scope; if you need a
   file changed outside it, say so in the report instead of doing it.
3. When you are done, write `{report_file}` with exactly these sections:

   ```
   ## Status
   ready

   ## Summary
   <what you did, 2-5 sentences>

   ## Files
   - <path> — <what changed>

   ## Checks
   - <command you actually ran> → <result you actually saw>

   ## Open issues
   - <anything unfinished, or "none">

   ## Self-review
   <mandatory. BEFORE filing this report, re-read ONLY what you added or changed
    and fix what you find: side effects on importers/callers; every Checks line
    re-run with real output; AI slop removed (extra comments, defensive
    try/except, deep nesting — constraints belong in structure, not prose).
    Write one line: what you reviewed and what you fixed, or exactly
    "no findings — checks re-run".>

   ## Attention
   <only when Status is blocked/failed: exactly one concrete question a person can answer>
   ```

4. Never claim a check you did not run. The lead verifies independently.
5. Do not ask the human anything unless you are blocked. If something is
   ambiguous, pick the most defensible option and record the assumption in
   "Open issues". If a person genuinely must decide, use "## Status: blocked"
   and put exactly one concrete question under "## Attention".
6. Start the report with a status line, in this closed vocabulary:
   `progress` (still working) · `waiting` (turn finished, waiting for a trigger)
   · `ready` (deliverable ready for review) · `blocked` (need a human decision)
   · `failed` (gave up) · `dropped` (nothing to do).
   A prose status is rejected by the verifier. If the work is still running when
   the deadline gets close, file `Status: progress` with what you have — the
   tower keeps waiting and extends the window; a progress report is never read
   as a failure or as a rework trigger.

{confirmed}## Task {task_id} ({batch})

{brief}
"""


# 2026-11-23: a long-lived role session was fed the full 60-line contract on
# every dispatch (19 copies in one session). The contract only changes when the
# report format or a role's scopes change, so after the first dispatch the
# follow-ups get this compact header and the full text is re-sent every
# `[contract] repeat_every` dispatches (default 5) as an anchor refresher.
CONTRACT_COMPACT = """## Contract (unchanged — same as your earlier dispatch)

Role `{role}` · task `{task_id}` · batch `{batch}`. Your folder: `{role_dir}`.
Report file: `{report_file}` — same sections as before (## Status / ## Summary /
## Files / ## Checks / ## Open issues / ## Self-review / ## Attention), and the
same closed status vocabulary: progress, waiting, ready, blocked, failed, dropped.
Still running when the deadline gets close? File `Status: progress` with what you
have — the tower keeps waiting and extends the window.
Write scope: {scopes}
Never claim a check you did not run. Blocked means exactly one concrete question
under "## Attention".

{confirmed}## Task {task_id} ({batch})

{brief}
"""

# v0.8 — the restate gate (playbook point 7: "voice brain-dump, then restate it").
# The first answer is deliberately *not* work: it is the role's own words about
# the brief, so a misread costs one short turn instead of a full dispatch.
RESTATE_CONTRACT = """## Restatement gate (read this first)

You are the **{role}** role inside a control-tower workflow. This turn is a
**restatement only**. Do not start the work, do not write deliverables, do not
run commands.

1. Workspace: `{workspace}`. Your own folder: `{role_dir}`.
2. Read the brief below and answer in your own words, in exactly these sections:

   ```
   ## Restatement
   <what you are being asked to do, 3-6 sentences, in your own words — not a copy>

   ## Assumptions
   - <the readings of the brief you had to pick between, one per line>
   - <or "none">

   ## Done looks like
   <the single observable fact that would make you call this finished>
   ```

3. If the brief is too ambiguous to restate, say so plainly in
   `## Restatement` and put the one question you need under `## Assumptions`.
4. Nothing else: no files, no checks, no status line. A person accepts the
   restatement with `tower restate ok {task_id}` before any work is dispatched.

## Task {task_id} ({batch})

{brief}
"""


def _auto_group(cfg: TowerConfig, task: Task) -> str:
    """v3 §4: one task = one group, so a task never inherits another's history."""
    if not getattr(cfg, "group_auto", True):
        return ""
    try:
        group = groups.create(cfg, task.brief.strip().splitlines()[0][:80] or task.id, [task.role])
    except OSError:
        return ""
    groups.attach(cfg, group.id, task.id)
    return group.id


DRY_RUN_STATUS = "dry-run (nothing written)"

# A task a person already closed. A settling turn must not reopen it: on 0.18.2 an
# in-flight async turn overwrote `accepted` with `reported` and triage reworked it
# back to `rework`, throwing away the acceptance (T-0023).
FROZEN_STATUSES = frozenset({STATUS_ACCEPTED, STATUS_FAILED, "dropped"})


def closed_task(board: Board, task_id: str) -> Task | None:
    """The task as it is on disk when a person already closed it, else None."""
    current = board.load(task_id)
    return current if current.status in FROZEN_STATUSES else None


def note_late_report(board: Board, task: Task, report: dict[str, Any]) -> Task:
    """Keep a report that landed after the task was closed — as history only."""
    payload = {"at": now_ts(), "status": task.status, "state": task.state, "report": report}
    task.escalation["late_report"] = payload   # the durable copy
    task.late_report = payload                 # a board.Task field would make this one
    board.save(task)
    board.log("settle_ignored", task=task.id, status=task.status, phase="post",
              report_chars=len(str(report.get("raw") or "")))
    return task


LATE_STATUS = "late"
LATE_NOTE = "adapter still running; supervise will re-check"
# A turn that outlived its adapter timeout gets one more full budget — capped at
# an hour, because the customer's 30-minute turn was judged failed at 11:05 and
# kept writing artifacts until 11:42.
LATE_HORIZON_S = 3600.0


def _timed_out(result: Any) -> bool:
    """The adapter stopped waiting; the work it started may still be running."""
    return bool((result.meta or {}).get("timed_out")) or "timed out" in (result.text or "").lower()


def _cancel_turn(adapter: Any, result: Any) -> bool:
    """Best effort: ask the harness to stop the turn we stopped waiting for."""
    session_id = str((result.meta or {}).get("session_id") or "")
    cancel = getattr(adapter, "cancel", None)
    if not (session_id and callable(cancel)):
        return False
    try:
        cancel(session_id)
    except Exception:      # the turn is already over budget; a failed cancel changes nothing
        return False
    return True


def mark_late(board: Board, task: Task, result: Any, cancelled: bool) -> Task:
    """A timeout is not a verdict: keep the task open until the horizon."""
    hard_deadline = (datetime.now() + timedelta(seconds=LATE_HORIZON_S)).strftime(_TS_FMT)
    task.escalation["late"] = {
        "since": now_ts(),
        "hard_deadline": hard_deadline,
        "cancelled": cancelled,
        "output": (result.text or "")[:500],
    }
    board.log("adapter_timeout", task=task.id, role=task.role, cancelled=cancelled,
              hard_deadline=hard_deadline)
    board.transition(task, LATE_STATUS, note=LATE_NOTE, output=(result.text or "")[:2000])
    return task


def recheck_late(cfg: TowerConfig, board: Board, now: datetime | None = None) -> list[dict[str, Any]]:
    """Settle every `late` task: the report landed, or the hard horizon is past."""
    now_dt = now or datetime.now()
    results: list[dict[str, Any]] = []
    for task in board.all():
        if task.status != LATE_STATUS:
            continue
        report = _late_report(cfg, task)
        if report is not None:
            task.report = report
            task.state = report.get("state", "")
            task.attention = report.get("attention")
            board.transition(task, STATUS_REPORTED,
                             note="late report recovered after the adapter timeout", report=report)
            board.push_report(task, report)
            board.log("late_recovered", task=task.id, role=task.role)
            results.append({"task": task.id, "outcome": "recovered"})
            continue
        deadline = _ts((task.escalation.get("late") or {}).get("hard_deadline", ""))
        if deadline is not None and now_dt >= deadline:
            board.transition(task, STATUS_FAILED,
                             note="adapter never finished within the hard horizon")
            board.log("late_failed", task=task.id, role=task.role, horizon_s=LATE_HORIZON_S)
            results.append({"task": task.id, "outcome": "failed"})
            continue
        results.append({"task": task.id, "outcome": "still-late"})
    return results


def build_task(
    cfg: TowerConfig,
    role_name: str,
    brief: str,
    *,
    batch: str = "",
    idem_key: str = "",
    mandate: dict[str, Any] | None = None,
) -> Task:
    """A task with the next id, built in memory and never saved.

    `Board.create()` cannot express a dry run: it writes the task file, and the
    id *is* the highest file on disk, so a preview would burn a T-id. This is
    `create()` up to the save; `dispatch(dry_run=True)` accepts such a task.
    """
    role = cfg.roles[role_name]
    return Task(
        id=next_task_id(cfg),
        role=role_name,
        brief=brief,
        batch=batch,
        write_scopes=list(role.write_scopes),
        max_rounds=role.max_rounds,
        idem_key=idem_key,
        mandate=dict(mandate or {}),
    )


def preview_group(cfg: TowerConfig, task: Task, group: str = "") -> str:
    """The group a real dispatch would use, without creating or attaching one."""
    if group:
        return group
    if task.group:
        return task.group
    return groups.next_id(cfg) if getattr(cfg, "group_auto", True) else ""


GROUP_REFUSAL = "no such group {group} in {workspace} — run `tower group list`"


def require_group(cfg: TowerConfig, group_id: str):
    """The room a dispatch names has to exist in this workspace.

    `groups.attach()` used to swallow the miss (the CLI's `_group_for` catches
    and returns ""), so the task kept a phantom group id, the job was enqueued,
    and the worker only logged `missing-task` while the board still showed a
    task that never moved. Returns the group, or raises the refusal.
    """
    try:
        return groups.load(cfg, group_id)
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise ValueError(GROUP_REFUSAL.format(
            group=group_id, workspace=cfg.workspace)) from exc


def group_refusal(cfg: TowerConfig, group_id: str) -> str:
    """`""` when the room exists, else the line the dispatch refuses with."""
    if not group_id:
        return ""
    try:
        require_group(cfg, group_id)
    except ValueError as exc:
        return str(exc)
    return ""


PROGRESS_STATUS = "progress"
PROGRESS_HINT = (
    "if the work is still running, file `Status: progress` in {task}-REPORT.md "
    "with what you have; the tower will keep waiting."
)
_TS_FMT = "%Y-%m-%dT%H:%M:%S"


def _ts(text: str) -> datetime | None:
    """A tower timestamp as local wall-clock: `created_at` carries an offset,
    `due_at` (from parse_due) does not, and the two are compared to each other."""
    for fmt in (_TS_FMT + "%z", _TS_FMT):
        try:
            return datetime.strptime((text or "").strip(), fmt).replace(tzinfo=None)
        except ValueError:
            continue
    return None


def is_progress_report(report: dict[str, Any] | None) -> bool:
    """A heartbeat, not a result: `Status: progress`, or nothing filed yet.

    2026-11-24 (aimigao-zh): a 38-minute verification was judged "no report"
    while it was still running. Neither shape proves work is done, so neither
    may settle a task, fail it, or trigger a rework.
    """
    if not report:
        return False
    if str(report.get("state") or "").strip().lower() == PROGRESS_STATUS:
        return True
    return not (report.get("summary") or report.get("files") or report.get("checks"))


def progress_window(task: Task) -> timedelta:
    """The slice a progress report buys: the window the task was given."""
    start, due = _ts(task.created_at), _ts(task.due_at)
    if start is None or due is None:
        return timedelta(0)
    return max(timedelta(0), due - start)


def record_progress(board: Board, task: Task, report: dict[str, Any]) -> dict[str, Any]:
    """File a progress report: keep waiting, extend one slice, settle nothing.

    The slice is fixed at the first heartbeat — recomputing it from an already
    extended deadline would double the wait on every report.
    """
    before = task.due_at
    due = _ts(before)
    escal = dict(task.escalation or {})
    progress = dict(escal.get("progress") or {})
    slice_s = int(progress.get("slice_s") or progress_window(task).total_seconds())
    if due is not None:
        task.due_at = (due + timedelta(seconds=slice_s)).strftime(_TS_FMT)
    progress["seen"] = int(progress.get("seen", 0)) + 1
    progress["slice_s"] = slice_s
    progress["at"] = now_ts()
    progress["summary"] = str(report.get("summary") or "")[:300]
    progress["due_before"] = before
    progress["due_after"] = task.due_at
    escal["progress"] = progress
    task.escalation = escal
    task.report = report
    task.state = PROGRESS_STATUS
    task.overdue = False
    board.save(task)
    board.log("progress_report", task=task.id, role=task.role, seen=progress["seen"],
              due_before=before, due_after=task.due_at)
    return progress


def progress_window_notice(cfg: TowerConfig, board: Board,
                           now: datetime | None = None) -> list[dict[str, Any]]:
    """One nudge per task at the halfway point of its deadline.

    Half a deadline with nothing filed is the moment to ask for a
    `Status: progress` line — not the moment to declare a stall. One-shot per
    task: `escalation.half_note_at` records that the ask went out.
    """
    stamp = (now or datetime.now()).replace(tzinfo=None)
    out: list[dict[str, Any]] = []
    for task in board.all():
        if task.status not in (STATUS_CLAIMED, STATUS_IN_PROGRESS):
            continue
        if (task.escalation or {}).get("half_note_at"):
            continue
        if is_progress_report(task.report):
            continue
        start, due = _ts(task.created_at), _ts(task.due_at)
        if start is None or due is None:
            continue
        half = start + (due - start) / 2
        if stamp < half:
            continue
        text = PROGRESS_HINT.format(task=task.id)
        task_dir = cfg.task_dir(task.id)
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "progress-hint.md").write_text(text + "\n", encoding="utf-8")
        escal = dict(task.escalation or {})
        escal["half_note_at"] = now_ts()
        task.escalation = escal
        board.save(task)
        board.notify({"event": "progress_hint", "task": task.id,
                      "summary": f"[tower] {task.id}: {text}"})
        board.log("progress_window_notice", task=task.id, role=task.role,
                  due_at=task.due_at, half_at=half.strftime(_TS_FMT))
        out.append({"task": task.id, "due_at": task.due_at})
    return out


def _confirmed_block(task: Task) -> str:
    """The restatement a person already accepted, carried into the work prompt."""
    if not (task.restate_ok and task.restatement.strip()):
        return ""
    return (
        "## Confirmed understanding (a person accepted this before you started)\n\n"
        f"{task.restatement.strip()}\n\n"
        "Work to that understanding. If it contradicts the brief below, do not\n"
        "guess: report `## Status: blocked` with the one question that resolves it.\n\n"
    )


def _contract_path(cfg: TowerConfig, role_name: str):
    return cfg.state_dir / "contracts" / f"{role_name}.json"


def _contract_key(role_dir, scopes: str) -> str:
    """What makes the contract text change: the template, the folder, the scopes.

    The report file name carries the task id, so it is deliberately left out —
    including it would make every dispatch look like a new contract.
    """
    material = "\x00".join([CONTRACT, str(role_dir), scopes])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def contract_plan(cfg: TowerConfig, task: Task) -> dict:
    """Full contract or compact header? Pure: reads state, never writes it.

    Compact only when the same role already got this exact contract text at least
    once and fewer than `contract_repeat_every` dispatches have gone by since the
    last full send. Anything new or different (first job, edited scopes, edited
    report format) sends the full thing again.
    """
    role_dir = cfg.role_dir(task.role)
    report_file = role_dir / f"{task.id}-REPORT.md"
    scopes = ", ".join(f"`{s}`" for s in task.write_scopes) or "(the whole workspace)"
    key = _contract_key(role_dir, scopes)
    repeat = int(getattr(cfg, "contract_repeat_every", 5) or 0)
    plan = {"compact": False, "key": key, "reason": "first dispatch for this role"}
    if repeat <= 0:
        plan["reason"] = "contract.repeat_every=0 (always full)"
        return plan
    path = _contract_path(cfg, task.role)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return plan
    if not isinstance(state, dict) or state.get("key") != key:
        plan["reason"] = "contract text changed since the last dispatch"
        return plan
    since_full = int(state.get("since_full", 0) or 0)
    if since_full >= repeat:
        plan["reason"] = f"full re-send every {repeat} dispatches"
        return plan
    plan.update(
        compact=True,
        reason=f"contract sent in full {since_full} dispatch(es) ago",
        since=str(state.get("since", "")),
    )
    return plan


def record_contract(cfg: TowerConfig, task: Task, plan: dict) -> None:
    """Remember what this role was sent, so the next dispatch can go compact."""
    path = _contract_path(cfg, task.role)
    path.parent.mkdir(parents=True, exist_ok=True)
    previous: dict = {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            previous = loaded
    except (OSError, ValueError):
        previous = {}
    same = previous.get("key") == plan.get("key")
    if plan.get("compact") and same:
        since_full = int(previous.get("since_full", 0) or 0) + 1
        since = str(previous.get("since", ""))
    else:
        since_full = 0
        since = now_ts()
    path.write_text(
        json.dumps(
            {"key": plan.get("key"), "since": since, "since_full": since_full,
             "last_task": task.id, "last_full": since},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def render_prompt(cfg: TowerConfig, task: Task, compact: bool = False) -> str:
    role = cfg.roles[task.role]
    role_dir = cfg.role_dir(task.role)
    scopes = ", ".join(f"`{s}`" for s in task.write_scopes) or "(the whole workspace)"
    template = CONTRACT_COMPACT if compact else CONTRACT
    prompt = template.format(
        role=task.role,
        workspace=cfg.workspace,
        role_dir=role_dir,
        scopes=scopes,
        report_file=role_dir / f"{task.id}-REPORT.md",
        task_id=task.id,
        batch=task.batch or "ad-hoc",
        confirmed=_confirmed_block(task),
        brief=task.brief,
    )
    # Playbook point 3: the role inherits what its past accepted jobs taught —
    # as a one-line-per-skill index, never the bodies (context window guard).
    from .skills import library_index_lines as skills_library_lines
    from .skills import skill_index_lines

    index = skill_index_lines(cfg, task.role)
    if index:
        prompt += (
            "\n## Skills from jobs you already did well\n\n"
            + "\n".join(index)
            + "\nEach line names a skill under `"
            + str(role_dir / "skills")
            + "/` — open one for the full steps. Follow them as the default way of "
            "working; deviate only with the reason recorded in the report.\n"
        )
    # pstack-derived work discipline: the compact index only (context guard);
    # a full card is one `tower principles <name>` away.
    if getattr(cfg, "principles", True):
        from .principles import scaffold_section

        prompt += scaffold_section()

    # Role charter: roles/<role>/AGENT.md was written by `tower role add` but
    # never read at dispatch — a dead file. Inject it (truncated) so charter
    # edits apply to every future dispatch.
    charter_path = role_dir / "AGENT.md"
    try:
        charter = charter_path.read_text(encoding="utf-8").strip()
    except OSError:
        charter = ""
    if charter:
        limit = 1500
        if len(charter) > limit:
            charter = charter[:limit] + "\n…(truncated)"
        prompt += "\n## Role charter\n\n" + charter + "\n"

    # Deploy discipline: any bot that touches a live server file gets the guard
    # rules in its prompt (2026-11-11: an unsynced online edit was silently
    # overwritten by a normal upload).
    for deploy_name in ("docs/deploy-rules.md", "DEPLOY-RULES.md"):
        deploy_path = Path(cfg.workspace) / deploy_name
        try:
            deploy_rules = deploy_path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if deploy_rules:
            limit = 2000
            if len(deploy_rules) > limit:
                deploy_rules = deploy_rules[:limit] + "\n…(truncated)"
            prompt += "\n## Deploy rules（部署纪律）\n\n" + deploy_rules + "\n"
        break

    # Workspace standing rules: docs/bug-report-protocol.md, docs/RULES.md or
    # RULES.md — whichever exists first. 全塔台规则改一处、每次派单全体生效。
    for rules_name in ("docs/bug-report-protocol.md", "docs/RULES.md", "RULES.md"):
        rules_path = Path(cfg.workspace) / rules_name
        try:
            rules = rules_path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if rules:
            limit = 2000
            if len(rules) > limit:
                rules = rules[:limit] + "\n…(truncated; full text: " + rules_name + ")"
            prompt += "\n## Standing rules（全塔常设规则）\n\n" + rules + "\n"
        break

    # v3 §5: the company-wide library is cross-role — a frontend lesson that a
    # backend needs is invisible to the per-role index above. Index only, never
    # bodies: the token cost of a skill is paid by the bot that opens it.
    extra = skills_library_lines(cfg, task.brief, limit=3)
    if extra:
        prompt += (
            "\n## Company skills that may apply\n\n" + "\n".join(extra)
            + "\nOpen one with `tower skill show <name>` when it matters; do not "
            "paste it back into the report.\n"
        )

    # v3 §3: a dispatched task inherits the kickoff package it was cut from.
    if task.group:
        section = kickoff.package_section(cfg, task.group, task.role)
        if section:
            prompt += section

    if task.due_at:
        prompt += (
            "\n## Deadline\n\n"
            + task.due_at
            + " — the lane leader is asked for your progress when this passes. "
            "Report `blocked` early instead of waiting.\n"
        )
    return prompt


def render_restate_prompt(cfg: TowerConfig, task: Task) -> str:
    """The restatement prompt: same task metadata, no deliverables asked for."""
    role_dir = cfg.role_dir(task.role)
    return RESTATE_CONTRACT.format(
        role=task.role,
        workspace=cfg.workspace,
        role_dir=role_dir,
        task_id=task.id,
        batch=task.batch or "ad-hoc",
        brief=task.brief,
    )


def _late_report(cfg: TowerConfig, task: Task) -> dict[str, Any] | None:
    """The report a timed-out role wrote anyway — None when it really produced none.

    An empty or header-only file is not evidence of work: parse it and require a
    summary, a file list or a check before the task leaves the failure bucket.
    """
    path = cfg.role_dir(task.role) / f"{task.id}-REPORT.md"
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text:
        return None
    report = parse_report(text)
    if not (report.get("summary") or report.get("files") or report.get("checks")):
        return None
    return report


def dispatch(
    cfg: TowerConfig,
    board: Board,
    task: Task,
    dry_run: bool = False,
    extra_options: dict[str, Any] | None = None,
    timeout_s: float | None = None,
    due: str = "",
    group: str = "",
) -> Task:
    """Run one task through its role's adapter.

    `timeout_s` caps a single adapter turn below the role's own timeout — the
    synchronous triage retry path uses it so one bad role cannot hang the lead
    for the role's full (often 30 min) budget (B12).
    """
    role = cfg.roles[task.role]
    if not dry_run:
        closed = closed_task(board, task.id)
        if closed is not None:
            board.log("settle_ignored", task=task.id, status=closed.status, phase="pre")
            return closed
    if dry_run:
        # A preview is read-only: no task file, no task dir, no group attach, no
        # T-id (the id is the highest file on disk) and no G-id consumed. The
        # task comes from build_task(), which never saves; the status carries
        # the marker the CLI prints.
        if group or task.group:
            require_group(cfg, group or task.group)
        task.group = preview_group(cfg, task, group)
        task.status = DRY_RUN_STATUS
        if not task.due_at:
            from .supervise import parse_due

            task.due_at = parse_due(due or cfg.default_due or "4h")
        board.log("dispatch_dry_run", task=task.id, role=task.role, group=task.group,
                  due_at=task.due_at)
        return task
    # v0.18 (v3 §3/§7): a group and a deadline are part of every dispatch, not
    # something the caller may forget. The package check is the one gate that
    # can refuse a dispatch outright — that is the point of a kickoff.
    if not task.group:
        task.group = group or _auto_group(cfg, task)
    elif group and group != task.group:
        task.group = group
    if task.group:
        require_group(cfg, task.group)
        kickoff.attach_task(cfg, task.group, task.id)
    if not task.due_at:
        from .supervise import parse_due

        task.due_at = parse_due(due or cfg.default_due or "4h")
    problems = kickoff.validate(kickoff.load_package(cfg, task.group)) if task.group else ["no group"]
    if cfg.kickoff_require and problems:
        raise ValueError(
            "no task package for this dispatch — run `tower kickoff` first:\n- "
            + "\n- ".join(problems)
        )
    board.save(task)

    plan = contract_plan(cfg, task)
    prompt = render_prompt(cfg, task, compact=bool(plan.get("compact")))

    task_dir = cfg.task_dir(task.id)
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "prompt.md").write_text(prompt, encoding="utf-8")
    (task_dir / "brief.md").write_text(task.brief, encoding="utf-8")
    board.log(
        "dispatch_contract",
        task=task.id,
        role=task.role,
        compact=bool(plan.get("compact")),
        reason=plan.get("reason", ""),
        prompt_len=len(prompt),
        group=task.group,
        due_at=task.due_at,
    )

    board.transition(task, STATUS_CLAIMED, note=f"dispatched to {role.name}")

    options = dict(cfg.adapter_options.get(role.adapter) or {})
    options.update(extra_options or {})
    adapter = get_adapter(role.adapter, options)

    board.transition(task, STATUS_IN_PROGRESS, note=f"adapter={adapter.name}")
    req = AdapterRequest(
        role=task.role,
        prompt=prompt,
        cwd=cfg.role_dir(task.role),
        timeout_s=min(role.timeout_s, timeout_s) if timeout_s else role.timeout_s,
        env=role.env,
        task_id=task.id,
        options=options,
    )
    result = adapter.run(req)

    (task_dir / f"adapter-{adapter.name}.txt").write_text(result.text, encoding="utf-8")
    (task_dir / "adapter-meta.json").write_text(
        json.dumps(result.meta or {}, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if result.exit_code == 0:
        # Only a delivered prompt counts: a crashed turn never read the contract.
        record_contract(cfg, task, plan)

    if result.exit_code != 0:
        # B7 (2026-11-23): a bot that outlived the adapter timeout still writes
        # its report — T-0202 ran 52 turns, was judged failed at 12:01, and the
        # complete REPORT landed at 12:44. A report on disk is work to verify,
        # not a failure, so hand it to the acceptance gate instead.
        late = _late_report(cfg, task)
        closed = closed_task(board, task.id)
        if closed is not None:
            return note_late_report(board, closed, late or {})
        if late is not None:
            board.log("late_report", task=task.id, role=task.role,
                      exit_code=result.exit_code, report_chars=len(late.get("raw") or ""))
            task.report = late
            task.state = late.get("state", "")
            task.attention = late.get("attention")
            board.add_assignment(task, adapter.name, result.exit_code)
            board.transition(
                task, STATUS_REPORTED,
                note=f"adapter exit {result.exit_code} but the report file landed — verify, do not trust",
                report=late,
            )
            board.push_report(task, late)
            return task
        if _timed_out(result):
            # P1-C (2026-11-24): the timeout only stopped us waiting. Marking the
            # task failed while the turn kept writing made "failed" a lie, and the
            # person reading the board had no way to know files were still coming.
            return mark_late(board, task, result, cancelled=_cancel_turn(adapter, result))
        board.transition(task, STATUS_FAILED, note=f"adapter exit {result.exit_code}", output=result.text[:2000])
        return task

    report = parse_report(result.text)
    closed = closed_task(board, task.id)
    if closed is not None:
        return note_late_report(board, closed, report)
    board.add_assignment(task, adapter.name, result.exit_code)
    if is_progress_report(report):
        # 2026-11-24 (aimigao-zh): `Status: progress` is the lane saying "still
        # working". Filing it as a report settled the task and then failed its
        # verification — the tower punished a lane for answering honestly.
        board.transition(task, STATUS_IN_PROGRESS, note="progress report — still running")
        record_progress(board, task, report)
        return task
    task.report = report
    task.state = report.get("state", "")
    task.attention = report.get("attention")
    board.transition(task, STATUS_REPORTED, note="report filed", report=report)
    board.push_report(task, report)
    return task


def dispatch_restate(
    cfg: TowerConfig,
    board: Board,
    task: Task,
    dry_run: bool = False,
) -> Task:
    """Ask a role to restate the brief; dispatch no work (playbook point 7).

    The gate fails closed. A non-zero adapter exit, a missing `## Restatement`
    section, or an empty restatement leaves the task out of `restating`, so a
    silent or confused adapter can never be mistaken for agreement. A task that
    does reach `restating` has no report and cannot be accepted: only
    `tower restate ok <task>` unlocks the actual dispatch.
    """
    role = cfg.roles[task.role]
    if not dry_run:
        closed = closed_task(board, task.id)
        if closed is not None:
            board.log("settle_ignored", task=task.id, status=closed.status, phase="restate")
            return closed
    if task.group:
        require_group(cfg, task.group)
    prompt = render_restate_prompt(cfg, task)

    if dry_run:
        board.log("restate_dry_run", task=task.id, role=task.role, prompt_len=len(prompt))
        return task

    task_dir = cfg.task_dir(task.id)
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "restate-prompt.md").write_text(prompt, encoding="utf-8")

    board.transition(task, STATUS_CLAIMED, note=f"restatement requested from {role.name}")

    options = dict(cfg.adapter_options.get(role.adapter) or {})
    adapter = get_adapter(role.adapter, options)

    board.transition(task, STATUS_IN_PROGRESS, note=f"adapter={adapter.name} (restate only)")
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

    (task_dir / f"restate-{adapter.name}.txt").write_text(result.text, encoding="utf-8")
    board.add_assignment(task, adapter.name, result.exit_code)

    if result.exit_code != 0:
        board.transition(task, STATUS_FAILED, note=f"restate adapter exit {result.exit_code}", output=result.text[:2000])
        # a failed GATE is a person decision (rewrite the brief), not a retry:
        # auto-triage would re-send the same words and fail the same way.
        task.state = "blocked"
        task.attention = {
            "question": f"Restatement gate failed for {task.id} (adapter exit "
                        f"{result.exit_code}) — rewrite the brief, then re-dispatch.",
        }
        board.save(task)
        return task

    parsed = parse_restatement(result.text)
    if not parsed["restatement"]:
        board.transition(task, STATUS_FAILED, note="adapter returned no '## Restatement' section")
        # same as above: a gate that fails closed is a person decision
        task.state = "blocked"
        task.attention = {
            "question": f"Restatement gate failed for {task.id} — the adapter never "
                        "answered with a '## Restatement' section. Rewrite the brief, "
                        "then re-dispatch.",
        }
        board.save(task)
        return task

    closed = closed_task(board, task.id)
    if closed is not None:
        board.log("settle_ignored", task=task.id, status=closed.status, phase="restate")
        return closed

    task.restatement = parsed["restatement"]
    task.restated_at = now_ts()
    (task_dir / "restatement.md").write_text(result.text, encoding="utf-8")
    task.attention = {
        "question": f"Restatement for {task.id} — is this the brief you meant?",
        "options": [f"tower restate ok {task.id}"],
    }
    board.transition(task, STATUS_RESTATING, note="restatement filed; no work dispatched yet")
    board.log(
        "restatement",
        task=task.id,
        role=task.role,
        chars=len(task.restatement),
        assumptions=len(parsed["assumptions"]),
    )
    return task


# One source of truth for the findings section: the adversarial contract renders
# this heading and the parser looks up this key (T-0045 — a reviewer copying the
# contract verbatim used to score zero findings because the contract annotated it).
FINDINGS_HEADING = "## Findings"
FINDINGS_SECTION = "findings"
_HEADING_ANNOTATION = re.compile(r"\s+[—–-]\s+|[:：]")


def _section_key(heading: str) -> str:
    """Heading -> lookup key: case and annotations (`— …`, `:`, `…`) are ignored."""
    return _HEADING_ANNOTATION.split(heading.strip(), maxsplit=1)[0].strip().lower()


def _sections(text: str) -> dict[str, list[str]]:
    """Split a markdown answer into `## Heading` -> lines (normalised key)."""
    buckets: dict[str, list[str]] = {}
    section: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped.startswith("## "):
            section = _section_key(stripped[3:])
            buckets.setdefault(section, [])
            continue
        if section:
            buckets[section].append(line)
    return buckets


def _strip_bullet(line: str) -> str:
    s = line.strip()
    if s.startswith(("-", "*", "•")):
        s = s[1:].strip()
    return s


# A finding counts as proved when its proof line carries the command's output.
PROOF_MARKER = re.compile(r"(?:->|→)\s*output:", re.IGNORECASE)
COMMAND_MARKER = " | command: "
_FINDING_EMPTY = ("none", "n/a", "no", "-", "")


def parse_report(text: str) -> dict[str, Any]:
    """Parse the role's markdown report into a dict.

    Missing sections are kept as empty lists — the verifier decides whether that
    is acceptable (see config `accept_requires`). Findings are read from
    `## Findings`; when a reviewer never wrote that section, the bullets in
    Summary / Open issues that carry the ` | command: ` marker are used instead
    (T-0040: qa hid proved findings in Summary and the verdict read 0).
    """
    buckets = _sections(text)

    def items(name: str) -> list[str]:
        return [_strip_bullet(line) for line in buckets.get(name, []) if line.strip()]

    summary_lines = [l.strip() for l in buckets.get("summary", []) if l.strip()]
    status_lines = [l.strip() for l in buckets.get("status", []) if l.strip()]
    state = (status_lines[0] if status_lines else "").strip().strip("`").lower()
    attention_lines = []
    for line in buckets.get("attention", []):
        s2 = _strip_bullet(line)
        if not s2 or s2.lower() in ("none", "n/a", "no", "-"):
            continue
        attention_lines.append(s2)
    question = attention_lines[0] if attention_lines else ""
    has_section = FINDINGS_SECTION in buckets
    findings = [f for f in items(FINDINGS_SECTION) if f.lower() not in _FINDING_EMPTY]
    if not has_section:
        findings = [f for name in ("summary", "open issues") for f in items(name)
                    if COMMAND_MARKER in f]
    self_review_lines = [l.strip() for l in buckets.get("self-review", []) if l.strip()]
    return {
        "state": state,
        "summary": " ".join(summary_lines),
        "files": items("files"),
        "checks": items("checks"),
        "open_issues": items("open issues"),
        "attention": {"question": question} if question else None,
        # v0.10 — the self-review gate (pstack: 写完 ≠ 完成，审查通过才算) and the
        # adversarial findings format (each entry must carry a command + its output)
        "self_review": " ".join(self_review_lines),
        "findings": findings,
        "findings_section": has_section,
        "findings_fallback": not has_section and bool(findings),
        "raw": text,
    }


def parse_restatement(text: str) -> dict[str, Any]:
    """Parse the answer to the restate gate (see `RESTATE_CONTRACT`).

    An empty `restatement` means the role did not answer the question that was
    asked; the caller must treat that as a failure, not as agreement.
    """
    buckets = _sections(text)
    restatement = "\n".join(line.rstrip() for line in buckets.get("restatement", [])).strip()
    assumptions = []
    for line in buckets.get("assumptions", []):
        item = _strip_bullet(line)
        if item and item.lower() not in ("none", "n/a", "-"):
            assumptions.append(item)
    done = " ".join(line.strip() for line in buckets.get("done looks like", []) if line.strip())
    return {"restatement": restatement, "assumptions": assumptions, "done": done, "raw": text}
