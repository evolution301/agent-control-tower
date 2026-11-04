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
from .board import (
    Board,
    PERSON_STATES,
    STATUS_ACCEPTED,
    STATUS_PENDING,
    STATUS_REPORTED,
    STATUS_REWORK,
)
from .config import ensure_layout, load_config
from .dispatch import dispatch
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
        print(f"unknown role {role!r}; known: {', '.join(sorted(cfg.roles))}")
        return 2
    brief = args.brief or sys.stdin.read()
    if not brief.strip():
        print("empty brief")
        return 2
    key = getattr(args, "key", "") or ""
    if key:
        existing = board.find_by_idem(key)
        if existing is not None:
            print(f"idempotent hit: task {existing.id} already exists for key {key!r}")
            return 0
    mandate = {
        "requester": getattr(args, "requester", "") or "operator",
        "strength": getattr(args, "strength", "") or "explicit",
        "policy": "tower.toml",
    }
    task = board.create(role, brief.strip(), batch=args.batch or "", idem_key=key, mandate=mandate)
    task = dispatch(cfg, board, task, dry_run=args.dry_run)
    print(f"task {task.id} role={task.role} status={task.status}")
    if task.report:
        print("\n--- report ---")
        print(f"summary: {task.report.get('summary', '')[:400]}")
        print(f"files: {len(task.report.get('files', []))} | checks: {len(task.report.get('checks', []))}")
        v = verify_report(cfg, task)
        print(f"verify: {'PASS' if v.ok else 'FAIL'} {v.problems or ''}")
    if args.json:
        _print(task.to_dict(), as_json=True)
    return 0


def cmd_batch(args) -> int:
    """Dispatch the same brief to several roles."""
    cfg = _cfg(args)
    ensure_layout(cfg)
    board = Board(cfg)
    brief = args.brief or ""
    rc = 0
    for i, role in enumerate(args.roles):
        if role not in cfg.roles:
            print(f"skip unknown role {role!r}")
            rc = 2
            continue
        task = board.create(role, brief, batch=args.batch or "")
        task = dispatch(cfg, board, task, dry_run=args.dry_run)
        print(f"{task.id} {role} -> {task.status}")
        if cfg.serial_requests and i < len(args.roles) - 1:
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


def cmd_doctor(args) -> int:
    cfg = _cfg(args)
    problems = []
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
    s.set_defaults(func=cmd_dispatch)

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
    return p


def main(argv: list[str] | None = None) -> int:
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
