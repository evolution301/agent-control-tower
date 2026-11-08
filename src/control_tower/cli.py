"""`tower` command line interface.

Design goals: every command is one verb, reads/writes only the workspace, and
prints something a human can paste into a chat.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__
from .analytics import build_report as build_bi_report, render_text as render_bi_text
from .roles import add_role, config_file, list_roles, remove_role
from .board import (
    Board,
    PERSON_STATES,
    STATUS_ACCEPTED,
    STATUS_PENDING,
    STATUS_REPORTED,
    STATUS_RESTATING,
    STATUS_REWORK,
)
from .config import ensure_layout, load_config
from .board_view import render as render_board, watch as watch_board
from .dispatch import dispatch, dispatch_restate
from .converse import load_conversation, parse_messages, relay_report_messages, say
from .huddle import huddle as run_huddle
from .onboard import onboard as run_onboard
from .skills import rebuild_index, save_skill, skill_index_lines
from .supervise import due_human, overdue_tasks, parse_due, supervise
from .triage import redispatch_with_reasons, triage as run_triage, triage_task
from .snapshot import build_snapshot
from .verify import verify_report

ROLE_SCAFFOLD = """# {role}

You are the **{role}** role.

## Mission
{description}

## Rules
- Work only inside `roles/{role}/` unless the task says otherwise.
- Always write `{{task_id}}-REPORT.md` with Summary / Files / Checks / Open issues.
- Never claim a check you did not run.
- Do not ask the human questions; decide and record assumptions under "Open issues".
"""

TOWER_TOML = """# agent-control-tower config (YAML syntax, .toml name for familiarity)
name: my-team
workspace: .

# Roles are the only way work enters the system.
roles:
  backend:
    description: Implements data, APIs, integrations and infrastructure.
    adapter: mock            # mock | cli | dsh | openai
    write_scopes: ["src/", "server/"]
    max_rounds: 3
  frontend:
    description: Owns layout, components, styling and browser verification.
    adapter: mock
    write_scopes: ["src/web/"]
    max_rounds: 3
  qa:
    description: Independent verification. Reproduces claims, files defects.
    adapter: mock
    write_scopes: []
    max_rounds: 2

# Optional: commands that must pass before the tower accepts any report.
# Replace with real checks. `echo` is used so `tower demo` works everywhere.
verify_commands:
  - "echo ok"

# Adapter options (all optional):
# adapters:
#   cli:
#     command: ["claude", "-p", "{prompt_file}"]
#   openai:
#     base_url: "https://api.openai.com/v1"
#     model: "gpt-4.1-mini"
#     api_key_env: "OPENAI_API_KEY"
#   dsh:
#     base_url: "http://127.0.0.1:65163"
#     sessions: { lead: "session-...", backend: "session-..." }

# For fragile/remote hosts: dispatch batches strictly one at a time.
serial_requests: false
request_gap_s: 1.5
"""


def _cfg(args):
    """Which tower this command talks to — and it must never change by accident.

    1. `--workspace` on the command line;
    2. a `tower.toml` found by walking up from cwd;
    3. the bound tower (`~/.tower/binding.json`) — the seamless reconnection after
       a plugin update that lost the aim;
    4. the project selected with `tower projects use`.

    Whichever tower is used gets recorded as the binding, so the next update can
    reconnect to it instead of guessing.
    """
    from . import registry

    explicit = getattr(args, "global_workspace", None) or getattr(args, "workspace", None)
    if explicit:
        return load_config(explicit)
    try:
        return load_config(os.getcwd())
    except FileNotFoundError:
        pass
    target = registry.effective_workspace(None)
    if target is not None:
        return load_config(target)
    raise FileNotFoundError(
        "no tower.toml here (or in any parent) and no bound/active project — "
        "run `tower init .`, or `tower projects use <name>`"
    )


def _bind(cfg):
    """Record the tower that did real work — never on read-only commands.

    Binding is what a later plugin update reconnects to. It is written on the
    commands that *act*, not on `doctor`/`status`, so `doctor` can still report a
    mismatch between the configured aim and the last tower that actually worked.
    """
    try:
        from . import registry

        registry.record_binding(cfg.workspace, source="cli")
    except Exception:
        pass
    return cfg


def _say(args, message: str):
    """Human-facing line: stdout normally, stderr when the caller asked for JSON
    so that stdout stays a single parseable JSON document."""
    stream = sys.stderr if getattr(args, "json", False) else sys.stdout
    print(message, file=stream)


def _print(obj, as_json: bool = False):
    if as_json:
        print(json.dumps(obj, ensure_ascii=False, indent=2))
    else:
        if isinstance(obj, str):
            print(obj)
        else:
            print(json.dumps(obj, ensure_ascii=False, indent=2))


def cmd_init(args) -> int:
    root = Path(args.path or os.getcwd()).resolve()
    root.mkdir(parents=True, exist_ok=True)
    cfg_path = root / "tower.toml"
    if cfg_path.exists() and not args.force:
        print(f"refusing to overwrite {cfg_path} (use --force)")
        return 1
    cfg_path.write_text(TOWER_TOML, encoding="utf-8")
    for role in ("backend", "frontend", "qa"):
        role_dir = root / "roles" / role
        role_dir.mkdir(parents=True, exist_ok=True)
        (role_dir / "AGENT.md").write_text(
            ROLE_SCAFFOLD.format(role=role, description={"backend": "Build and maintain the server side.",
                                                         "frontend": "Build and maintain the browser UI.",
                                                         "qa": "Verify other roles' claims independently."}[role]),
            encoding="utf-8",
        )
    (root / ".gitignore").write_text(".tower/\n__pycache__/\n*.pyc\n", encoding="utf-8")
    print(f"initialised tower in {root}")
    print("next: tower dispatch backend \"describe the task\"")
    return 0


def cmd_dispatch(args) -> int:
    cfg = _cfg(args)
    _bind(cfg)
    ensure_layout(cfg)
    board = Board(cfg)
    role = args.role
    if role not in cfg.roles:
        _say(args, f"unknown role {role!r}; known: {', '.join(sorted(cfg.roles))}")
        return 2
    brief = args.brief or sys.stdin.read()
    if not brief.strip():
        _say(args, "empty brief")
        return 2
    key = getattr(args, "key", "") or ""
    if key:
        existing = board.find_by_idem(key)
        if existing is not None:
            if getattr(args, "json", False):
                _print(existing.to_dict(), as_json=True)
            else:
                print(f"idempotent hit: task {existing.id} already exists for key {key!r}")
            return 0
    mandate = {
        "requester": getattr(args, "requester", "") or "operator",
        "strength": getattr(args, "strength", "") or "explicit",
        "policy": "tower.toml",
    }
    try:
        due_at = parse_due(getattr(args, "due", "") or "")
    except ValueError as exc:
        _say(args, str(exc))
        return 2
    task = board.create(role, brief.strip(), batch=args.batch or "", idem_key=key, mandate=mandate)
    task.due_at = due_at
    board.save(task)
    # --async (or dispatch_mode: async / role override): enqueue and return the id
    # in under a second; a tower worker runs the adapter and settles the task.
    async_mode = (getattr(args, "async_", False)
                  or cfg.effective_dispatch_mode(task.role) == "async")
    if async_mode and not args.dry_run:
        from .worker import enqueue, ensure_worker

        kind = "restate" if getattr(args, "restate", False) else "dispatch"
        job = enqueue(cfg, kind, task.id)
        ensure_worker(cfg)
        if args.json:
            _print({"id": task.id, "role": task.role, "queued": True,
                    "job": job["id"], "mode": "async",
                    "settle": "tower inbox (async_settled)"}, as_json=True)
            return 0
        print(f"queued {task.id} (job {job['id']}) — the worker will run it")
        print("settlement lands in `tower inbox`; watch progress with `tower show "
              + task.id + "`")
        return 0
    if getattr(args, "restate", False):
        task = dispatch_restate(cfg, board, task, dry_run=args.dry_run)
        if args.json:
            _print(task.to_dict(), as_json=True)
            return 0
        print(f"task {task.id} role={task.role} status={task.status}")
        if task.restatement:
            print("\n--- restatement (no work has run yet) ---")
            print(task.restatement[:1200])
            print(f"\nnext: tower restate ok {task.id}   # work is dispatched only after this")
        return 0
    task = dispatch(cfg, board, task, dry_run=args.dry_run)
    relayed = [] if args.dry_run else relay_report_messages(cfg, board, task)
    if not args.dry_run and not getattr(args, "no_auto", False):
        _auto_triage(cfg, board, task)
    if args.json:
        # Machine mode: stdout carries exactly one JSON document and nothing else
        # so adapters (the DSH bundle) can parse it. The human report below must
        # never leak into --json output.
        doc = task.to_dict()
        doc["relayed"] = relayed
        _print(doc, as_json=True)
        return 0
    print(f"task {task.id} role={task.role} status={task.status}")
    if due_at:
        print(f"due: {due_at}  ({due_human(task)})")
    for entry in relayed:
        print(f"relayed to {entry['to']}: conversation {entry['conversation']}")
    if task.report:
        print("\n--- report ---")
        print(f"summary: {task.report.get('summary', '')[:400]}")
        print(f"files: {len(task.report.get('files', []))} | checks: {len(task.report.get('checks', []))}")
        v = verify_report(cfg, task)
        print(f"verify: {'PASS' if v.ok else 'FAIL'} {v.problems or ''}")
    return 0


def cmd_batch(args) -> int:
    """Dispatch the same brief to several roles."""
    cfg = _cfg(args)
    _bind(cfg)
    ensure_layout(cfg)
    board = Board(cfg)
    roles = list(args.roles)
    brief = args.brief or ""
    # `tower batch backend qa "ship it"`: argparse cannot tell the trailing brief
    # from another role name (roles is nargs="+"), so an unknown trailing name is
    # taken as the brief instead of being silently skipped.
    unknown = [role for role in roles if role not in cfg.roles]
    if unknown and not brief:
        brief = " ".join(unknown)
        roles = [role for role in roles if role in cfg.roles]
        _say(args, f"treating {brief!r} as the brief, not a role")
    if not roles:
        _say(args, "no known roles given")
        return 2
    if not brief.strip():
        _say(args, 'empty brief — pass it as --brief "..." or as the last argument')
        return 2
    rc = 0
    try:
        due_at = parse_due(getattr(args, "due", "") or "")
    except ValueError as exc:
        _say(args, str(exc))
        return 2
    for i, role in enumerate(roles):
        task = board.create(role, brief, batch=args.batch or "")
        task.due_at = due_at
        board.save(task)
        task = dispatch(cfg, board, task, dry_run=args.dry_run)
        relay_report_messages(cfg, board, task)
        if not args.dry_run and not getattr(args, "no_auto", False):
            _auto_triage(cfg, board, task)
        print(f"{task.id} {role} -> {task.status}")
        if cfg.serial_requests and i < len(roles) - 1:
            import time

            time.sleep(cfg.request_gap_s)
    return rc


def cmd_inbox(args) -> int:
    cfg = _cfg(args)
    board = Board(cfg)
    entries = board.inbox(unread_only=not args.all)
    if not entries:
        print("inbox empty")
        return 0
    for e in entries:
        r = e.get("report") or {}
        print(f"[{e['ts']}] {e['task']} ({e['role']})")
        print(f"  summary: {(r.get('summary') or '')[:200]}")
        print(f"  files: {len(r.get('files') or [])} | checks: {len(r.get('checks') or [])}")
    if args.read:
        n = board.mark_inbox_read()
        print(f"marked {n} read")
    return 0


def cmd_board(args) -> int:
    """Terminal panel: what needs a person, what is in flight, who is doing what."""
    cfg = _cfg(args)
    if args.watch:
        watch_board(cfg, interval=args.interval)
        return 0
    snap = build_snapshot(cfg, Board(cfg))
    print(render_board(snap, colour=None if not args.json else False))
    if args.json:
        _print(snap.as_dict(), as_json=True)
    return 0


def cmd_panel(args) -> int:
    """Local web panel (read-only), bound to 127.0.0.1 by default.

    No `--port`: the panel keeps a stable URL — it reuses the port this workspace
    used last time, else 8787, else the next free one, and remembers the result.
    """
    from .panel import serve

    cfg = _cfg(args)
    serve(cfg, host=args.host, port=args.port, open_browser=args.open_browser)
    return 0


def cmd_attention(args) -> int:
    """Only what needs a person — the rest can stay quiet."""
    cfg = _cfg(args)
    board = Board(cfg)
    tasks = board.needs_person()
    if not tasks:
        print("nothing needs you — all tasks are working, idle or accepted")
        return 0
    for t in tasks:
        question = (t.attention or {}).get("question", "")
        print(f"{t.id}  {t.role:<10} {t.state or t.status:<8} {t.brief[:50]}")
        if question:
            print(f"      → {question}")
    return 0


def cmd_accept(args) -> int:
    cfg = _cfg(args)
    board = Board(cfg)
    task = board.load(args.task)
    # A restating task has no report by construction; accepting it would accept
    # a claim that was never made. The gate has exactly one exit: `restate ok`.
    if task.status == STATUS_RESTATING:
        _say(args, f"{task.id} is still restating — no work has run. "
                   f"Accept the restatement first: tower restate ok {task.id}")
        return 2
    v = verify_report(cfg, task)
    if not v.ok:
        board.transition(task, STATUS_REWORK, note="verification failed: " + "; ".join(v.problems))
        print("REJECTED -> rework")
        for p in v.problems:
            print("  -", p)
        return 1
    board.transition(task, STATUS_ACCEPTED, note=args.note or "verified and accepted", verification=v.as_dict())
    print(f"{task.id} ACCEPTED")
    return 0


def cmd_rework(args) -> int:
    cfg = _cfg(args)
    board = Board(cfg)
    task = board.load(args.task)
    board.transition(task, STATUS_REWORK, note=args.note or "rework requested")
    task.round += 1
    if task.round > task.max_rounds:
        board.transition(task, STATUS_REWORK, note=f"max rounds ({task.max_rounds}) exceeded — escalate to human")
        print(f"{task.id} still in rework; round budget exhausted, escalate to the human")
        return 1
    task = dispatch(cfg, board, task)
    print(f"{task.id} round {task.round} -> {task.status}")
    return 0


def cmd_restate(args) -> int:
    """Accept a task's restatement, then dispatch the work (playbook point 7).

    This is the only transition out of `restating`: no report exists yet, so the
    task cannot be accepted, and the work prompt carries the accepted
    restatement as `## Confirmed understanding`.
    """
    cfg = _cfg(args)
    _bind(cfg)
    board = Board(cfg)
    task = board.load(args.task)
    if task.status != STATUS_RESTATING:
        _say(args, f"{task.id} is not waiting for a restatement (status={task.status})")
        return 2
    if not (task.restatement or "").strip():
        _say(args, f"{task.id} has no restatement recorded — re-run dispatch with --restate")
        return 2
    task.restate_ok = True
    task.attention = None
    board.transition(task, STATUS_PENDING, note=args.note or "restatement accepted by a person")
    board.log("restatement_accepted", task=task.id, role=task.role)
    task = dispatch(cfg, board, task)
    if getattr(args, "json", False):
        _print(task.to_dict(), as_json=True)
        return 0
    print(f"{task.id} restatement accepted — work dispatched")
    print(f"status={task.status}")
    if task.report:
        print("\n--- report ---")
        print(f"summary: {task.report.get('summary', '')[:400]}")
        v = verify_report(cfg, task)
        print(f"verify: {'PASS' if v.ok else 'FAIL'} {v.problems or ''}")
    return 0


def cmd_say(args) -> int:
    """Relay one bot-to-bot message. Both halves are filed, never whispered."""
    cfg = _cfg(args)
    _bind(cfg)
    ensure_layout(cfg)
    board = Board(cfg)
    message = args.message or sys.stdin.read()
    if not message.strip():
        _say(args, "empty message")
        return 2
    try:
        entry = say(cfg, board, args.sender, args.to, message.strip(), task_id=args.task)
    except KeyError as exc:
        _say(args, str(exc))
        return 2
    except ValueError as exc:
        _say(args, str(exc))
        return 2
    if getattr(args, "json", False):
        _print(entry, as_json=True)
        return 0
    print(f"conversation {entry['conversation']}: {args.sender} → {args.to}"
          f"  [{'delivered' if entry['delivered'] else 'NOT delivered — recorded on the message'}]")
    print(f"archived for the lead: {entry['conversation']}")
    if not entry["delivered"]:
        print(f"detail: {entry['detail']}")
    return 0


def cmd_huddle(args) -> int:
    """Open a room for up to six roles, two rounds, then everyone works."""
    cfg = _cfg(args)
    _bind(cfg)
    ensure_layout(cfg)
    board = Board(cfg)
    try:
        record = run_huddle(cfg, board, list(args.roles), args.topic, rounds=args.rounds)
    except (KeyError, ValueError) as exc:
        _say(args, str(exc))
        return 2
    if getattr(args, "json", False):
        _print(record, as_json=True)
        return 0
    print(f"huddle {record['id']}: {record['topic']}")
    print(f"roles: {', '.join(record['roles'])} · rounds: {record['rounds']}")
    print(f"minutes: {record['path']}")
    for turn in record["turns"]:
        if turn["round"] == 2 and turn.get("commitments"):
            for c in turn["commitments"]:
                if c != "none":
                    print(f"  commitment · {turn['role']}: {c[:100]}")
    return 0


def cmd_projects(args) -> int:
    """Many towers, one switcher: list, register, forget, or make one active."""
    from . import registry

    action = args.projects_command
    if action == "list" or action is None:
        projects = registry.load_projects()
        active = registry.get_active()
        active_name = active["name"] if active else ""
        if getattr(args, "json", False):
            _print({"active": active_name, "projects": projects}, as_json=True)
            return 0
        if not projects:
            print("no projects registered — `tower projects add <path>` or `tower projects scan`")
            return 0
        width = max(len(p["name"]) for p in projects)
        for item in projects:
            mark = "●" if item["name"] == active_name else " "
            row = registry.summarise(item)
            print(f"{mark} {item['name']:<{width}}  {item['path']}")
            if row["ok"]:
                print(f"    bots {row['bots']} · tasks {row['tasks']} · open {row['open']}"
                      f" · needs you {row['needs_you']} · overdue {row['overdue']}")
            else:
                print(f"    broken: {row['error']}")
        return 0

    if action == "scan":
        found = registry.discover([args.root] if args.root else None)
        added = []
        for path in found:
            if any(Path(p["path"]).resolve() == path for p in registry.load_projects()):
                continue
            added.append(registry.add_project(path))
        if getattr(args, "json", False):
            _print({"found": [str(p) for p in found], "added": added}, as_json=True)
            return 0
        print(f"scanned {args.root or '~'} · found {len(found)} tower(s) · added {len(added)}")
        for item in added:
            print(f"  + {item['name']}  {item['path']}")
        return 0

    if action == "add":
        try:
            entry = registry.add_project(args.path, name=args.name or "", color=args.color or "")
        except ValueError as exc:
            _say(args, str(exc))
            return 2
        print(f"registered {entry['name']}  {entry['path']}")
        return 0

    if action == "remove":
        try:
            entry = registry.remove_project(args.name)
        except KeyError as exc:
            _say(args, str(exc))
            return 2
        print(f"forgot {entry['name']}")
        return 0

    if action == "use":
        try:
            entry = registry.set_active(args.name)
        except KeyError as exc:
            _say(args, str(exc))
            return 2
        print(f"active project: {entry['name']}  {entry['path']}")
        return 0

    _say(args, f"unknown projects subcommand {action!r}")
    return 2


def cmd_worker(args) -> int:
    """Run the async queue worker: claim → run adapter → settle → notify.

    Started automatically by `dispatch --async` when no live heartbeat exists;
    run it by hand to keep a visible process (or with --once in scripts/CI).
    """
    from .worker import run_worker

    cfg = _cfg(args)
    ensure_layout(cfg)
    return run_worker(cfg, once=getattr(args, "once", False))


def cmd_relay(args) -> int:
    """Deliver a message to any DSH session (or `--to tower` = this inbox)."""
    from .adapters import get_adapter
    from .relay import send_relay

    cfg = _cfg(args)
    ensure_layout(cfg)
    board = Board(cfg)
    adapter_name = getattr(args, "adapter", "") or "dsh"
    options = dict(cfg.adapter_options.get(adapter_name) or {})
    adapter = get_adapter(adapter_name, options)
    try:
        entry = send_relay(
            cfg, board, adapter,
            to=args.to, message=args.message,
            sender=getattr(args, "from_", "") or "",
            reply=getattr(args, "reply", "") or "",
            await_receipt=getattr(args, "await_receipt", False),
            timeout_s=int(getattr(args, "timeout_s", 300)),
        )
    except ValueError as exc:
        _say(args, str(exc))
        return 2
    if args.json:
        _print(entry, as_json=True)
        return 0
    status = "delivered" if entry["delivered"] else "NOT delivered"
    print(f"relay {entry['id']} → {entry['to']} ({entry['resolution']}): {status}")
    if entry.get("watch"):
        print(f"awaiting receipt for {args.timeout_s}s — outcome lands in `tower inbox`")
    if not entry["delivered"]:
        print(entry.get("detail", ""))
        return 3
    return 0


def cmd_stats(args) -> int:
    """BI view: throughput, verified rate, lead time, per-role and supervision."""
    if getattr(args, "all", False):
        from . import registry

        data = registry.overview()
        if getattr(args, "json", False):
            _print(data, as_json=True)
            return 0
        totals = data["totals"]
        print(f"all projects · generated {data['generated_at']}")
        print(f"projects {totals['projects']}  bots {totals['bots']}  tasks {totals['tasks']}  "
              f"accepted {totals['accepted']}  open {totals['open']}  "
              f"needs you {totals['needs_you']}  overdue {totals['overdue']}"
              + (f"  broken {totals['broken']}" if totals["broken"] else ""))
        width = max((len(row["name"]) for row in data["projects"]), default=4)
        print("")
        print(f"{'project':<{width}}  bots  tasks  acc  open  need  late")
        for row in data["projects"]:
            if not row["ok"]:
                print(f"{row['name']:<{width}}  BROKEN: {row['error']}")
                continue
            print(f"{row['name']:<{width}}  {row['bots']:>4}  {row['tasks']:>5}  "
                  f"{row['accepted']:>3}  {row['open']:>4}  {row['needs_you']:>4}  {row['overdue']:>4}")
        return 0

    cfg = _cfg(args)
    board = Board(cfg)
    report = build_bi_report(cfg, board, days=args.days)
    if getattr(args, "json", False):
        _print(report, as_json=True)
        return 0
    print(render_bi_text(report))
    return 0


def _triage_dispatch(cfg, board, task, reasons: str):
    """Re-dispatch a rejected job with its own failure reasons attached.

    Thin wrapper over `triage.redispatch_with_reasons` (the shared path also used
    by the async worker). This takes cfg, so it does NOT match
    `triage.triage_task`'s 3-arg `dispatch_fn(board, task, reasons)` contract
    directly — the callers below bind cfg with a closure (the formal fix for the
    2026-11-08 exit-1 crash: a failed task on the board made the next dispatch
    die in auto-triage with "missing 1 required positional argument: reasons").
    """
    redispatch_with_reasons(cfg, board, task, reasons)


def _auto_triage(cfg, board, task):
    """Close the ordinary loop for one task: verify + accept, or rework, or escalate."""
    if not getattr(cfg, "auto_triage", True):
        return None
    return triage_task(cfg, board, task, dispatch_fn=(lambda b, t, r: _triage_dispatch(cfg, b, t, r)))


def cmd_triage(args) -> int:
    """The tower's own follow-up pass: verify, accept, retry — escalate only when stuck."""
    cfg = _cfg(args)
    board = Board(cfg)
    result = run_triage(cfg, board, dispatch_fn=(lambda b, t, r: _triage_dispatch(cfg, b, t, r)))
    if getattr(args, "json", False):
        _print(result, as_json=True)
        return 0
    if not result["touched"]:
        print("nothing to triage — the queue is empty")
        return 0
    print(f"triaged {result['touched']} item(s): "
          + ", ".join(f"{k} {v}" for k, v in sorted(result["outcomes"].items())))
    for item in result["results"]:
        if item["outcome"] not in ("done", "waiting"):
            print(f"  {item['task']}  {item['outcome']}"
                  + (f"  round {item['round']}" if item.get("round") else ""))
    if result["needs_person"]:
        print(f"\nstill needs a person: {', '.join(result['needs_person'])}")
    else:
        print("\nnothing needs a person")
    return 0


def cmd_supervise(args) -> int:
    """Pull up every open task past its deadline, in its own session."""
    cfg = _cfg(args)
    _bind(cfg)
    ensure_layout(cfg)
    board = Board(cfg)
    result = supervise(cfg, board)
    if getattr(args, "json", False):
        _print(result, as_json=True)
        return 0
    if not result["touched"]:
        overdue = overdue_tasks(board, cfg)
        if not overdue:
            print("nothing overdue")
        return 0
    print(f"supervised {result['touched']} overdue task(s):")
    for r in result["results"]:
        print(f"  {r['task']}  {r['role']:<10} {r['outcome']:<18} nudges={r['nudges']}")
        if r.get("summary"):
            print(f"      {r['summary'][:160]}")
    return 0


def cmd_skill(args) -> int:
    """Save an accepted task as a reusable skill, or list what a role knows."""
    cfg = _cfg(args)
    _bind(cfg)
    board = Board(cfg)
    if args.skill_command == "save":
        try:
            info = save_skill(cfg, board, args.task)
        except (KeyError, ValueError) as exc:
            _say(args, str(exc))
            return 2
        if getattr(args, "json", False):
            _print(info, as_json=True)
            return 0
        print(f"skill saved: {info['path']}")
        for line in info["index_lines"]:
            print(f"  {line}")
        return 0
    # list
    roles = list(cfg.roles) if not getattr(args, "role", "") else [args.role]
    for role in roles:
        lines = skill_index_lines(cfg, role)
        if not lines:
            continue
        print(f"{role}:")
        for line in lines:
            print(f"  {line}")
    if not any(skill_index_lines(cfg, r) for r in roles):
        _say(args, "no skills yet — finish a job, accept it, then: "
                   "tower skill save <task>")
    return 0


def cmd_onboard(args) -> int:
    """Interview the operator (or read notes.md) and grow the team."""
    cfg = _cfg(args)
    path = config_file(cfg.workspace)
    try:
        result = run_onboard(
            path,
            notes_path=Path(args.source) if getattr(args, "source", "") else None,
            adapter=args.adapter or "dsh",
            force=getattr(args, "force", False),
        )
    except (KeyError, ValueError) as exc:
        _say(args, str(exc))
        return 2
    if getattr(args, "json", False):
        _print(result, as_json=True)
        return 0
    print(f"team grown: {', '.join(result['roles'])}")
    print(f"plan: {result['plan']}")
    print("next: tower role list   # then wire sessions: tower bootstrap")
    return 0


def cmd_status(args) -> int:
    cfg = _cfg(args)
    board = Board(cfg)
    tasks = board.all()
    if args.json:
        _print([t.to_dict() for t in tasks], as_json=True)
        return 0
    if not tasks:
        print("no tasks yet")
        return 0
    width = max(len(t.id) for t in tasks)
    for t in tasks:
        state = f"{t.state}/{t.status}" if t.state else t.status
        due = due_human(t)
        flag = " ⚠" if t.overdue else ""
        print(f"{t.id:<{width}}  {t.role:<10} {state:<18} rounds={t.round} {due:<14}{flag} {t.brief[:60]}")
    return 0


def cmd_show(args) -> int:
    cfg = _cfg(args)
    board = Board(cfg)
    task = board.load(args.task)
    _print(task.to_dict(), as_json=True)
    return 0


def cmd_log(args) -> int:
    cfg = _cfg(args)
    if not cfg.ledger_path.exists():
        print("no ledger yet")
        return 0
    lines = cfg.ledger_path.read_text(encoding="utf-8").splitlines()
    for line in lines[-args.tail :]:
        print(line)
    return 0


def cmd_demo(args) -> int:
    """End-to-end offline demo: dispatch -> report -> verify -> accept."""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cmd_init(argparse.Namespace(path=str(root), force=True))
        cfg = load_config(root)
        board = Board(cfg)
        task = board.create("backend", "Produce a placeholder artifact pipeline.")
        task = dispatch(cfg, board, task)
        print(f"[1] dispatched {task.id} -> {task.status}")
        v = verify_report(cfg, task)
        print(f"[2] verify: {'PASS' if v.ok else 'FAIL'} {v.problems or ''}")
        board.transition(task, STATUS_ACCEPTED, note="demo accept", verification=v.as_dict())
        print(f"[3] accepted {task.id}")
        print("[4] ledger lines:", len(cfg.ledger_path.read_text(encoding='utf-8').splitlines()))
    return 0


SHELL_BUILTINS = {
    "echo", "cd", "true", "false", ":", "dir", "copy", "type", "set",
    "rem", "exit", "if", "for", "cat", "printf",
}


def _command_available(command: str) -> bool:
    """Whether a verify command can actually run on this machine."""
    import shutil

    token = command.strip().split()[0].strip(chr(34) + chr(39)) if command.strip() else ""
    if not token or token in SHELL_BUILTINS:
        return True
    if "/" in token or chr(92) in token:
        return Path(token).exists()
    return shutil.which(token) is not None


def cmd_doctor(args) -> int:
    cfg = _cfg(args)
    problems = []
    warnings = []
    for command in cfg.verify_commands:
        if not _command_available(command):
            problems.append(
                "verify command %r: %r is not on PATH" % (command, command.strip().split(" ")[0])
            )
    # playbook point 2: few, well-trained bots — size and overlap are smell.
    if len(cfg.roles) > 5:
        warnings.append(
            f"{len(cfg.roles)} roles — a few well-trained bots beat a crowd; consider merging"
        )
    seen: dict[str, str] = {}
    for name, role in sorted(cfg.roles.items()):
        for scope in role.write_scopes:
            if scope in seen:
                warnings.append(
                    f"roles {seen[scope]!r} and {name!r} both write {scope!r} — split the scope "
                    "or merge the roles"
                )
            else:
                seen[scope] = name
    # playbook point 4: credentials belong to the role, not the global environment.
    for name, role in cfg.roles.items():
        if role.env and not (cfg.role_dir(name) / ".env").exists():
            warnings.append(
                f"role {name}: carries env vars but has no roles/{name}/.env — credentials "
                "resolve from the global environment; give the role its own .env"
            )
    # the aim check: a plugin update must not leave the tools pointed at another tower
    try:
        from . import registry

        configured = Path(cfg.workspace).resolve()
        bound = registry.read_binding()
        if bound and Path(bound["path"]).resolve() != configured:
            warnings.append(
                f"aim: this workspace is {configured} but this machine last used "
                f"{bound['path']} ({bound.get('recorded_at', 'unknown time')}) — "
                "check which tower you mean before dispatching, or `tower projects use`"
            )
        elif bound is None:
            warnings.append(
                "aim: no bound tower recorded yet — the first dispatch will bind this one"
            )
    except Exception:
        pass
    for name, role in cfg.roles.items():
        if role.adapter not in ("mock", "cli", "dsh", "openai"):
            problems.append(f"role {name}: unknown adapter {role.adapter}")
        opts = cfg.adapter_options.get(role.adapter) or {}
        if role.adapter == "cli" and not opts.get("command"):
            problems.append(f"role {name}: cli adapter has no command")
        if role.adapter == "openai" and not (opts.get("api_key") or os.environ.get(opts.get("api_key_env", "OPENAI_API_KEY"))):
            problems.append(f"role {name}: openai adapter has no API key in env")
        if role.adapter == "dsh" and not (opts.get("sessions") or {}).get(name):
            problems.append(f"role {name}: dsh adapter has no session id")
    print("workspace:", cfg.workspace)
    print("roles:", ", ".join(sorted(cfg.roles)))
    for warning in warnings:
        print("warning:", warning)
    print("problems:", problems or "none")
    return 0 if not problems else 1


def cmd_role_list(args) -> int:
    cfg = _cfg(args)
    rows = list_roles(cfg)
    if getattr(args, "json", False):
        _print(rows, as_json=True)
        return 0
    if not rows:
        _say(args, 'no roles yet — add one: tower role add <name> --description "..."')
        return 0
    width = max(len(row["name"]) for row in rows)
    for row in rows:
        scope = ", ".join(row["write_scopes"]) or "-"
        session = row["session_id"] or "NO session id"
        print(f"{row['name']:<{width}}  {row['adapter']:<6} rounds={row['max_rounds']}  scope={scope}  {session}")
        if row["description"]:
            print(f"    {row['description']}")
    return 0


def cmd_role_add(args) -> int:
    cfg = _cfg(args)
    path = config_file(cfg.workspace)
    charter = Path(args.charter).read_text(encoding="utf-8") if args.charter else ""
    try:
        info = add_role(
            path,
            args.name,
            description=args.description,
            adapter=args.adapter,
            write_scopes=args.scope,
            max_rounds=args.max_rounds,
            timeout_s=args.timeout,
            session_id=args.session,
            charter=charter,
            force=args.force,
        )
    except (ValueError, FileNotFoundError) as exc:
        _say(args, f"error: {exc}")
        return 2
    if getattr(args, "json", False):
        _print(info, as_json=True)
        return 0
    verb = "replaced" if info["replaced"] else "added"
    scopes = ", ".join(info["write_scopes"]) or "-"
    print(f"role {info['name']} {verb} (adapter={info['adapter']}, scope={scopes})")
    print(f"charter: {info['charter']}")
    if info["session_id"]:
        print(f"session wired: {info['session_id']}")
    else:
        print("next: wire its bot session — tower role add %s ... --session <session-id>" % info["name"])
    return 0


def cmd_role_remove(args) -> int:
    cfg = _cfg(args)
    path = config_file(cfg.workspace)
    try:
        info = remove_role(path, args.name, purge=args.purge)
    except (ValueError, FileNotFoundError) as exc:
        _say(args, f"error: {exc}")
        return 2
    if getattr(args, "json", False):
        _print(info, as_json=True)
        return 0
    suffix = f" (roles/{info['name']}/ deleted)" if info["purged"] else " (charter kept; --purge deletes it)"
    print(f"role {info['name']} removed{suffix}")
    return 0


def cmd_bootstrap(args) -> int:
    from .bootstrap import run_bootstrap

    cfg = _cfg(args)
    path = config_file(cfg.workspace)
    try:
        summary = run_bootstrap(
            path,
            base_url=args.base_url or "",
            secret=args.secret or "",
            pin=not args.no_pin,
            smoke_role=args.smoke or "",
            delete=args.delete_session or "",
            workspace=cfg.workspace,
        )
    except (ValueError, FileNotFoundError) as exc:
        _say(args, f"error: {exc}")
        return 2
    if getattr(args, "json", False):
        _print(summary, as_json=True)
        return 1 if summary.get("error") else 0
    print("config: " + summary["config"])
    for step in summary["steps"]:
        print("  " + json.dumps(step, ensure_ascii=False))
    for row in summary.get("roles", []):
        print("  %-16s %-44s %s" % (row["role"], row["session_id"] or "(unresolved)", row["resolution"]))
    if summary.get("smoke"):
        print("smoke: exit=%s" % summary["smoke"]["exit_code"])
        print(summary["smoke"]["text"])
    if summary.get("error"):
        _say(args, "error: " + summary["error"])
        return 1
    return 0


def cmd_plugin_install(args) -> int:
    from .plugin_install import install

    try:
        summary = install(profile=args.profile, source=args.source or None, dry_run=args.dry_run)
    except (FileNotFoundError, ValueError, OSError) as exc:
        _say(args, f"error: {exc}")
        return 2
    if getattr(args, "json", False):
        _print(summary, as_json=True)
        return 0
    print("profile  : " + summary["profile"])
    version = summary["version"]
    if summary.get("previous_version"):
        version += " (was %s)" % summary["previous_version"]
    print("version  : " + version)
    print("bundles  : " + ("added" if summary["bundle_added"] else "already registered"))
    if summary.get("dry_run"):
        print("dry run  : nothing written")
        return 0
    print("installed: " + summary["installed_to"])
    print("next     : restart the DSH desktop app — the tower_* tools appear in new sessions")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tower", description="Dispatch tasks to role agents and verify their reports.")
    p.add_argument("--version", action="version", version=f"agent-control-tower {__version__}")
    p.add_argument("--workspace", dest="global_workspace", default=None,
                   help="tower workspace (defaults to cwd and walks up)")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("init", help="create a tower in a directory")
    s.add_argument("path", nargs="?")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("dispatch", help="create + run a task for one role")
    s.add_argument("role")
    s.add_argument("brief", nargs="?")
    s.add_argument("--batch", default="")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--json", action="store_true")
    s.add_argument("--key", default="", help="idempotency key: the same key never creates a second task")
    s.add_argument("--requester", default="", help="who asked for this work (recorded in the mandate)")
    s.add_argument("--strength", default="", choices=["", "explicit", "inferred"],
                   help="explicit (a person asked) or inferred (a rule allowed it)")
    s.add_argument("--restate", action="store_true",
                   help="ask the role to restate the brief first; no work runs until `tower restate ok`")
    s.add_argument("--no-auto", action="store_true",
                   help="do not auto-triage (leave the report for a human to accept)")
    s.add_argument("--async", dest="async_", action="store_true",
                   help="queue the task and return its id in under a second; a tower worker "
                        "runs the adapter and the settlement lands in `tower inbox`")
    s.add_argument("--due", default="",
                   help="deadline: 90m / 2h / 3d or '2026-11-09 18:00'; a silent overrun gets chased by `tower supervise`")
    s.set_defaults(func=cmd_dispatch)

    s = sub.add_parser("say", help="relay one bot-to-bot message; both halves are filed")
    s.add_argument("sender")
    s.add_argument("to")
    s.add_argument("message", nargs="?")
    s.add_argument("--task", default="", help="link the exchange to a task id")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_say)

    s = sub.add_parser("huddle", help="put up to six roles in one room for a bounded discussion")
    s.add_argument("roles", nargs="+")
    s.add_argument("--topic", default="")
    s.add_argument("--rounds", type=int, default=2, choices=[1, 2])
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_huddle)

    s = sub.add_parser("supervise", help="chase every open task past its deadline")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_supervise)

    s = sub.add_parser("worker", help="run the async queue worker (dispatch --async / relay receipts)")
    s.add_argument("--once", action="store_true", help="drain the current queue and exit")

    s = sub.add_parser("relay", help="deliver a message to any DSH session (or --to tower)")
    s.add_argument("to", help="target: session id, unique title substring, or the alias 'tower'")
    s.add_argument("-m", "--message", required=True, help="the message text")
    s.add_argument("--from", dest="from_", default="", help="label the sender (default: the tower name)")
    s.add_argument("--reply", default="", help="mark this as the answer to relay <id>")
    s.add_argument("--adapter", default="", help="adapter used for delivery (default: dsh)")
    s.add_argument("--await-receipt", action="store_true",
                   help="watch for a reply carrying the relay id; outcome lands in `tower inbox`")
    s.add_argument("--timeout-s", type=int, default=300, help="receipt watch budget (default 300s)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_relay)

    s = sub.add_parser("triage", help="the tower's own follow-up pass: verify, accept, retry, escalate")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_triage)

    s = sub.add_parser("stats", help="business view: throughput, verified rate, lead time, roles")
    s.add_argument("--days", type=int, default=14, help="throughput window in days (default 14)")
    s.add_argument("--all", action="store_true", help="aggregate every registered project")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_stats)

    s = sub.add_parser("projects", help="many towers, one switcher: list, scan, add, forget, use")
    ps = s.add_subparsers(dest="projects_command")
    plist = ps.add_parser("list", help="registered projects with their numbers")
    plist.add_argument("--json", action="store_true")
    plist.set_defaults(func=cmd_projects)
    sc = ps.add_parser("scan", help="find tower.toml under a root and register them")
    sc.add_argument("--root", default="")
    sc.add_argument("--json", action="store_true")
    sc.set_defaults(func=cmd_projects)
    pa = ps.add_parser("add", help="register one project directory")
    pa.add_argument("path")
    pa.add_argument("--name", default="")
    pa.add_argument("--color", default="")
    pa.set_defaults(func=cmd_projects)
    pr = ps.add_parser("remove", help="forget a project (files are untouched)")
    pr.add_argument("name")
    pr.set_defaults(func=cmd_projects)
    pu = ps.add_parser("use", help="make one project active (what tools and the panel follow)")
    pu.add_argument("name")
    pu.set_defaults(func=cmd_projects)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_projects)

    s = sub.add_parser("skill", help="distil accepted work into reusable skills")
    sk = s.add_subparsers(dest="skill_command", required=True)
    sks = sk.add_parser("save", help="turn one accepted task into roles/<role>/skills/<slug>.md")
    sks.add_argument("task")
    sks.add_argument("--json", action="store_true")
    sks.set_defaults(func=cmd_skill)
    skl = sk.add_parser("list", help="show each role's skill index (what dispatch injects)")
    skl.add_argument("--role", default="")
    skl.set_defaults(func=cmd_skill)

    s = sub.add_parser("onboard", help="interview the operator and grow the team from the answers")
    s.add_argument("--from", dest="source", default="", help="notes file: '## <role>' sections + 'scope:' lines")
    s.add_argument("--adapter", default="dsh", help="adapter for the new roles (default dsh)")
    s.add_argument("--force", action="store_true", help="replace roles that already exist")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_onboard)

    s = sub.add_parser("restate", help="accept a restatement so the work may start")
    rss = s.add_subparsers(dest="restate_command", required=True)
    rs = rss.add_parser("ok", help="confirm the role understood the brief, then dispatch the work")
    rs.add_argument("task")
    rs.add_argument("--note", default="")
    rs.add_argument("--json", action="store_true")
    rs.add_argument("--no-auto", action="store_true")
    rs.set_defaults(func=cmd_restate)

    s = sub.add_parser("batch", help="dispatch one brief to several roles")
    s.add_argument("roles", nargs="+")
    s.add_argument("--brief", default="")
    s.add_argument("--batch", default="")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--due", default="", help="deadline for every task in the batch")
    s.add_argument("--no-auto", action="store_true", help="do not auto-triage after each dispatch")
    s.set_defaults(func=cmd_batch)

    s = sub.add_parser("inbox", help="show reports filed by roles")
    s.add_argument("--all", action="store_true")
    s.add_argument("--read", action="store_true", help="mark all reports read")
    s.set_defaults(func=cmd_inbox)

    s = sub.add_parser("board", help="terminal panel (use --watch to keep it refreshing)")
    s.add_argument("--watch", action="store_true")
    s.add_argument("--interval", type=float, default=2.0)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_board)

    s = sub.add_parser("panel", help="local web panel on 127.0.0.1 (stable URL)")
    s.add_argument("--port", type=int, default=None,
                   help="force one port; by default the panel reuses its remembered port, else 8787, else +1")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--workspace", default=None, help="tower workspace (same as the global flag)")
    s.add_argument("--open-browser", action="store_true")
    s.set_defaults(func=cmd_panel)

    s = sub.add_parser("attention", help="list only the tasks that need a person")
    s.set_defaults(func=cmd_attention)

    s = sub.add_parser("accept", help="verify and accept a reported task")
    s.add_argument("task")
    s.add_argument("--note", default="")
    s.set_defaults(func=cmd_accept)

    s = sub.add_parser("rework", help="send a task back with feedback")
    s.add_argument("task")
    s.add_argument("--note", default="")
    s.set_defaults(func=cmd_rework)

    s = sub.add_parser("status", help="list tasks")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("show", help="show one task as JSON")
    s.add_argument("task")
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("log", help="tail the ledger")
    s.add_argument("--tail", type=int, default=20)
    s.set_defaults(func=cmd_log)

    s = sub.add_parser("demo", help="offline end-to-end demo (no model, no network)")
    s.set_defaults(func=cmd_demo)

    s = sub.add_parser("doctor", help="check the configuration")
    s.set_defaults(func=cmd_doctor)
    s = sub.add_parser("role", help="list, add or remove roles (bots)")
    rs = s.add_subparsers(dest="role_command", required=True)
    r = rs.add_parser("list", help="list roles and their wired session ids")
    r.add_argument("--json", action="store_true")
    r.set_defaults(func=cmd_role_list)
    r = rs.add_parser("add", help="add a role (bot): writes tower.toml + roles/<name>/AGENT.md")
    r.add_argument("name")
    r.add_argument("--description", default="")
    r.add_argument("--adapter", default="dsh", help="mock | cli | dsh | openai")
    r.add_argument("--scope", action="append", default=[], help="write scope, repeatable")
    r.add_argument("--max-rounds", type=int, default=2)
    r.add_argument("--timeout", type=int, default=1800)
    r.add_argument("--session", default="", help="DSH session id of this bot (wires adapters.dsh.sessions)")
    r.add_argument("--charter", default="", help="path to a markdown charter for roles/<name>/AGENT.md")
    r.add_argument("--force", action="store_true")
    r.add_argument("--json", action="store_true")
    r.set_defaults(func=cmd_role_add)
    r = rs.add_parser("remove", help="unregister a role (bot)")
    r.add_argument("name")
    r.add_argument("--purge", action="store_true", help="also delete roles/<name>/")
    r.add_argument("--json", action="store_true")
    r.set_defaults(func=cmd_role_remove)
    s = sub.add_parser("bootstrap", help="wire this machine in one run: harness URL, secret, role sessions")
    s.add_argument("--base-url", default="", help="harness URL (default: probe the listening ports)")
    s.add_argument("--secret", default="", help="base64 HMAC secret (default: env or the bridge scripts)")
    s.add_argument("--no-pin", action="store_true", help="do not write resolved session ids into tower.toml")
    s.add_argument("--smoke", default="", help="role to send one real brief to when the wiring is done")
    s.add_argument("--delete-session", default="", help="delete one session id (e.g. an accidental twin)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_bootstrap)
    s = sub.add_parser("plugin", help="install the dsh-tower bundle into a DSH profile")
    ps = s.add_subparsers(dest="plugin_command", required=True)
    pl = ps.add_parser("install", help="write the bundle into <profile>/node_modules and register it")
    pl.add_argument("--profile", default="desktop")
    pl.add_argument("--source", default="", help="local packages/dsh-tower checkout (default: download from GitHub)")
    pl.add_argument("--dry-run", action="store_true")
    pl.add_argument("--json", action="store_true")
    pl.set_defaults(func=cmd_plugin_install)
    return p


def main(argv: list[str] | None = None) -> int:
    # A GBK console cannot encode every charter line; degrade instead of crashing.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
