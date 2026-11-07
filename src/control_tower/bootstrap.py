"""One-shot machine wiring for a tower that drives live DSH sessions.

`tower bootstrap` does the whole manual checklist in one run:

1. find the desktop harness (scan listening ports, confirm with `session/list`),
2. recover the HMAC secret from an existing bridge script or the environment,
3. write `base_url` + `secret` into the tower config,
4. drop verify commands that cannot run on this machine,
5. resolve a session for every role (cache -> title match on the RPC row or the
   disk projection cache -> create), optionally pinning the ids in the config,
6. optionally send one real brief and wait for its report.

Every step is idempotent: running it twice changes nothing and costs one
`session/list`.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from .adapters.base import AdapterRequest
from .adapters.dsh import DshAdapter
from .config import load_config

SECRET_RE = re.compile(r'SECRET_B64\s*=\s*"([^"]+)"')
BASE_URL_LINE = re.compile(r'(?m)^(\s*base_url:\s*)"[^"]*"')
SECRET_LINE = re.compile(r'(?m)^(\s*secret:\s*)"[^"]*"')
BUILTINS = {
    "echo", "cd", "true", "false", ":", "dir", "copy", "type", "set", "rem",
    "exit", "if", "for", "cat", "printf", "test", "pwd",
}
SECRET_SOURCES = ("_dsh-extract/*.py", ".agents/*.py", "scripts/*.py", "*.py")


def listening_ports() -> list[int]:
    """Local TCP ports in LISTEN state, parsed from netstat (no extra deps)."""
    try:
        completed = subprocess.run(
            ["netstat", "-an"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    ports = set()
    for line in (completed.stdout or "").splitlines():
        fields = line.split()
        if not fields or fields[0].upper() not in ("TCP", "TCPV6"):
            continue
        if "LISTEN" not in line.upper() or len(fields) < 2:
            continue
        address = fields[1]
        port = address.rsplit(":", 1)[-1]
        if port.isdigit():
            ports.add(int(port))
    return sorted(ports)


def probe(base_url: str, secret: str, timeout: float = 20) -> tuple[str | None, dict]:
    """Probe one base_url. Returns ('harness'|'needs-secret'|None, reply).

    20s, not 3: a long-lived desktop host answers session/list with hundreds of
    rows, and a 3s socket timeout looked exactly like a wrong secret.
    """
    adapter = DshAdapter(base_url=base_url, secret=secret, wait_s=0.1, poll_s=0.1)
    reply = adapter.rpc("session/list", {"_request": {}}, timeout=timeout)
    error = str(reply.get("error") or "")
    if not error:
        return "harness", reply
    if error.startswith("http 401") or error.startswith("http 403"):
        return "needs-secret", reply
    return None, reply


def detect_base_url(secret: str = "", prefer: str = "") -> tuple[str | None, bool]:
    """Return (base_url, needs_secret). An explicit value wins."""
    if prefer:
        kind, _ = probe(prefer, secret)
        return prefer, kind == "needs-secret"
    fallback = None
    for port in listening_ports():
        base_url = "http://127.0.0.1:%d" % port
        kind, _ = probe(base_url, secret)
        if kind == "harness":
            return base_url, False
        if kind == "needs-secret" and fallback is None:
            fallback = base_url
    return (fallback, True) if fallback else (None, False)


def detect_secret(workspace: Path) -> str:
    """base64 HMAC secret from the environment or an existing bridge script."""
    env = os.environ.get("DSH_BRIDGE_SECRET", "").strip()
    if env:
        return env
    for pattern in SECRET_SOURCES:
        for file in sorted(workspace.glob(pattern)):
            try:
                text = file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            match = SECRET_RE.search(text)
            if match:
                return match.group(1)
    return ""


def apply_connection(config_path: Path, base_url: str = "", secret: str = "") -> list[str]:
    """Write base_url/secret into the config; returns what changed."""
    text = config_path.read_text(encoding="utf-8")
    changed = []
    if base_url:
        text, count = BASE_URL_LINE.subn(lambda m: m.group(1) + '"' + base_url + '"', text, count=1)
        if count:
            changed.append("base_url")
    if secret:
        text, count = SECRET_LINE.subn(lambda m: m.group(1) + '"' + secret + '"', text, count=1)
        if count:
            changed.append("secret")
    if changed:
        config_path.write_text(text, encoding="utf-8")
    return changed


def command_available(command: str) -> bool:
    token = command.strip().split(" ")[0].strip("\"'") if command.strip() else ""
    if not token or token in BUILTINS:
        return True
    if "/" in token or "\\" in token:
        return Path(token).exists()
    return shutil.which(token) is not None


def fix_verify_commands(config_path: Path) -> list[str]:
    """Replace verify commands that cannot run here. Returns the dropped ones."""
    cfg = load_config(config_path.parent)
    broken = [command for command in cfg.verify_commands if not command_available(command)]
    if not broken:
        return []
    lines = config_path.read_text(encoding="utf-8").splitlines(keepends=True)
    start = None
    for i, line in enumerate(lines):
        if line.strip() == "verify_commands:":
            start = i
            break
    if start is None:
        return broken
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].strip() and not lines[j][0].isspace():
            end = j
            break
    lines[start:end] = [
        "verify_commands:\n",
        '  - "echo ok"   # tower bootstrap: replace with a real check\n',
    ]
    config_path.write_text("".join(lines), encoding="utf-8")
    return broken


def adapter_for(config_path: Path, secret: str = "", base_url: str = "") -> DshAdapter:
    cfg = load_config(config_path.parent)
    options = dict(cfg.adapter_options.get("dsh") or {})
    return DshAdapter(
        base_url=base_url or options.get("base_url", ""),
        secret=secret or options.get("secret", ""),
        sessions=options.get("sessions") or {},
        role_titles=options.get("role_titles") or {},
        create_cwd=options.get("create_cwd", ""),
        projcache_dir=options.get("projcache_dir", ""),
        wait_s=float(options.get("wait_s", 1800)),
        poll_s=float(options.get("poll_s", 5)),
        report_file=options.get("report_file", "{task_id}-REPORT.md"),
    )


def resolve_roles(config_path: Path, secret: str = "", base_url: str = "", pin: bool = False) -> tuple[list[dict], DshAdapter]:
    """Resolve a session for every role and optionally pin the id in the config."""
    cfg = load_config(config_path.parent)
    adapter = adapter_for(config_path, secret=secret, base_url=base_url)
    rows = []
    pinned: dict[str, str] = {}
    for name in cfg.roles:
        request = AdapterRequest(role=name, prompt="", cwd=str(cfg.workspace), task_id="BOOTSTRAP")
        session_id, how = adapter.resolve_session(request)
        rows.append({"role": name, "session_id": session_id or "", "resolution": how})
        if session_id:
            pinned[name] = session_id
    if pin and pinned:
        from .roles import set_session

        for name, session_id in pinned.items():
            set_session(config_path, name, session_id)
    return rows, adapter


def delete_session(base_url: str, secret: str, session_id: str) -> dict:
    """Best-effort `session/delete` for one id (e.g. an accidentally created twin)."""
    adapter = DshAdapter(base_url=base_url, secret=secret, wait_s=0.1, poll_s=0.1)
    return adapter.rpc("session/delete", {"request": {"sessionId": session_id}})


def run_bootstrap(
    config_path: Path,
    *,
    base_url: str = "",
    secret: str = "",
    pin: bool = True,
    smoke_role: str = "",
    delete: str = "",
    workspace: Path | None = None,
) -> dict:
    """Run every wiring step and return a JSON-able summary."""
    cfg = load_config(config_path.parent)
    workspace = Path(workspace) if workspace else cfg.workspace
    summary: dict = {"config": str(config_path), "steps": []}

    if not secret:
        secret = detect_secret(workspace)
        summary["steps"].append({"step": "secret", "found": bool(secret)})
    detected, needs_secret = detect_base_url(secret=secret, prefer=base_url)
    summary["base_url"] = detected
    summary["needs_secret"] = needs_secret
    summary["steps"].append({"step": "base_url", "value": detected, "needs_secret": needs_secret})
    if not detected:
        summary["error"] = "no running DSH harness found - is the desktop app open?"
        return summary

    changed = apply_connection(config_path, base_url=detected, secret=secret)
    summary["steps"].append({"step": "config", "changed": changed})

    dropped = fix_verify_commands(config_path)
    summary["steps"].append({"step": "verify_commands", "dropped": dropped})

    rows, adapter = resolve_roles(config_path, secret=secret, base_url=detected, pin=pin)
    summary["roles"] = rows
    summary["pinned"] = pin
    summary["steps"].append({"step": "sessions", "resolved": sum(1 for r in rows if r["session_id"])})

    if delete:
        reply = delete_session(detected, secret, delete)
        summary["deleted"] = {"session": delete, "reply": reply}
        summary["steps"].append({"step": "delete", "session": delete, "ok": "error" not in reply})

    if smoke_role:
        prompt = "冒烟测试：把 `pwd` 的结果写进回执（summary / files / checks 三段），然后回一行摘要。"
        result = adapter.run(AdapterRequest(role=smoke_role, prompt=prompt, cwd=str(cfg.workspace), task_id="SMOKE"))
        summary["smoke"] = {"role": smoke_role, "exit_code": result.exit_code, "text": result.text[:400]}
        summary["steps"].append({"step": "smoke", "role": smoke_role, "exit_code": result.exit_code})
    return summary
