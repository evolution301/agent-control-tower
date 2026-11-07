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
    return load_config(args.workspace or os.getcwd())


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
    task = board.create(role, brief.strip(), batch=args.batch or "", idem_key=key, mandate=mandate)
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
    if args.json:
        # Machine mode: stdout carries exactly one JSON document and nothing else
        # so adapters (the DSH bundle) can parse it. The human report below must
        # never leak into --json output.
        _print(task.to_dict(), as_json=True)
        return 0
    print(f"task {task.id} role={task.role} status={task.status}")
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
    for i, role in enumerate(roles):
        task = board.create(role, brief, batch=args.batch or "")
        task = dispatch(cfg, board, task, dry_run=args.dry_run)
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
    """Local web panel (read-only), bound to 127.0.0.1 by default."""
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
        print(f"{t.id:<{width}}  {t.role:<10} {state:<18} rounds={t.round} {t.brief[:60]}")
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
    for command in cfg.verify_commands:
        if not _command_available(command):
            problems.append(
                "verify command %r: %r is not on PATH" % (command, command.strip().split(" ")[0])
            )
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
    p.add_argument("--workspace", help="tower workspace (defaults to cwd and walks up)")
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
    s.set_defaults(func=cmd_dispatch)

    s = sub.add_parser("restate", help="accept a restatement so the work may start")
    rss = s.add_subparsers(dest="restate_command", required=True)
    rs = rss.add_parser("ok", help="confirm the role understood the brief, then dispatch the work")
    rs.add_argument("task")
    rs.add_argument("--note", default="")
    rs.add_argument("--json", action="store_true")
    rs.set_defaults(func=cmd_restate)

    s = sub.add_parser("batch", help="dispatch one brief to several roles")
    s.add_argument("roles", nargs="+")
    s.add_argument("--brief", default="")
    s.add_argument("--batch", default="")
    s.add_argument("--dry-run", action="store_true")
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

    s = sub.add_parser("panel", help="local web panel on 127.0.0.1")
    s.add_argument("--port", type=int, default=8787)
    s.add_argument("--host", default="127.0.0.1")
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
