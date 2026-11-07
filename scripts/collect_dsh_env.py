#!/usr/bin/env python3
"""Collect the three values `tower` needs to drive live DSH sessions.

    base_url  - the desktop harness URL listening on this machine
    secret    - the base64 HMAC secret the harness bridge uses (may be empty)
    sessions  - every DSH session whose cwd is this workspace, with its title

Run it on the machine that hosts the DSH sessions; it prints the JSON and writes
it to a file. Nothing is uploaded anywhere.

    python scripts/collect_dsh_env.py --workspace . --out tower-env.json
    python scripts/collect_dsh_env.py --apply-to tower.toml

Cross-platform: Windows uses `netstat`/`tasklist`, macOS and Linux use `lsof`/`ps`.
Exit codes: 0 ok, 1 no DSH home, 2 no sessions found for the workspace.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import platform
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DSH_PROCESS_NAMES = ("DeepSeek Harness", "DeepSeek-Harness", "dsh")


def dsh_home() -> Path:
    override = os.environ.get("DSH_HOME")
    return Path(override) if override else Path.home() / ".dsh"


def default_out() -> Path:
    desktop = Path.home() / "Desktop"
    return (desktop if desktop.is_dir() else Path.home()) / "tower-env.json"


def find_secret(root: Path) -> str:
    env_secret = os.environ.get("DSH_BRIDGE_SECRET")
    if env_secret:
        return env_secret
    candidates = [
        root / "_dsh-extract" / "notify_lead.py",
        root / "_dsh-extract" / "bridge.py",
        root / ".agents" / "bridge.py",
        Path.home() / ".dsh" / "bridge.py",
    ]
    pattern = re.compile(r'SECRET_B64\s*=\s*"([^"]+)"')
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        match = pattern.search(text)
        if match:
            return match.group(1)
    return ""


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def auth_cookie(authority: str, secret_b64: str) -> str | None:
    if not secret_b64:
        return None
    padded = secret_b64.replace("-", "+").replace("_", "/")
    padded += "=" * (-len(padded) % 4)
    try:
        secret = base64.b64decode(padded)
    except (ValueError, base64.binascii.Error):  # type: ignore[attr-defined]
        return None
    issued = int(time.time() * 1000)
    payload = json.dumps(
        {"version": 1, "authority": authority, "issuedAt": issued, "expiresAt": issued + 2_592_000_000},
        separators=(",", ":"),
    )
    body = _b64url(payload.encode("utf-8"))
    signature = _b64url(hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest())
    name_hash = _b64url(hashlib.sha256(authority.encode("utf-8")).digest())
    return f"dsh-auth-{name_hash}=v1.{body}.{signature}"


def probe_port(port: int, secret_b64: str) -> tuple[bool, int]:
    authority = f"127.0.0.1:{port}"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Origin": f"http://{authority}",
    }
    cookie = auth_cookie(authority, secret_b64)
    if cookie:
        headers["Cookie"] = cookie
    envelope = json.dumps(
        {
            "type": "client-request",
            "rpcId": f"probe-{os.getpid()}-{port}",
            "method": "session/list",
            "payload": {"args": {"request": {}}},
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"http://{authority}/api/session/list", data=envelope, headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return True, response.status
    except urllib.error.HTTPError as error:
        # 401/403 still identifies the harness: it answered the RPC surface.
        return error.code in (401, 403), error.code
    except (urllib.error.URLError, OSError):
        return False, 0


def listening_ports_windows() -> list[int]:
    try:
        names = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=20
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    wanted = set()
    for line in names.splitlines():
        cells = [cell.strip('"') for cell in line.split('","')]
        if len(cells) < 2:
            continue
        if any(token.lower() in cells[0].strip('"').lower() for token in DSH_PROCESS_NAMES):
            wanted.add(cells[1].strip('"'))
    if not wanted:
        return []
    try:
        netstat = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=20
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    ports = set()
    for line in netstat.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[3].upper() != "LISTENING":
            continue
        if parts[4] in wanted:
            local = parts[1]
            if ":" in local:
                ports.add(int(local.rsplit(":", 1)[1]))
    return sorted(ports)


def listening_ports_posix() -> list[int]:
    pids = set()
    try:
        ps = subprocess.run(["ps", "-A", "-o", "pid=,comm="], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    for line in ps.splitlines():
        pid, _, command = line.strip().partition(" ")
        if any(token.lower() in command.lower() for token in DSH_PROCESS_NAMES):
            pids.add(pid)
    if not pids:
        return []
    try:
        lsof = subprocess.run(
            ["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-Fpn"], capture_output=True, text=True, timeout=20
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    ports = set()
    current_pid = None
    for line in lsof.splitlines():
        if line.startswith("p"):
            current_pid = line[1:]
        elif line.startswith("n") and current_pid in pids:
            name = line[1:]
            if ":" in name:
                try:
                    ports.add(int(name.rsplit(":", 1)[1]))
                except ValueError:
                    continue
    return sorted(ports)


def find_base_url(secret_b64: str) -> str | None:
    ports = listening_ports_windows() if platform.system() == "Windows" else listening_ports_posix()
    fallback = None
    for port in ports:
        ok, status = probe_port(port, secret_b64)
        if ok and 200 <= status < 300:
            return f"http://127.0.0.1:{port}"
        if ok and fallback is None:
            fallback = f"http://127.0.0.1:{port}"
    return fallback


def session_index(home: Path) -> list[dict]:
    cache = home / "storages" / "session_projcache" / "sessions"
    rows: list[dict] = []
    if not cache.is_dir():
        return rows
    for path in sorted(cache.glob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            continue
        record = doc.get("record") or {}
        identity = record.get("identity") or {}
        cwd = identity.get("cwd")
        if not cwd:
            continue
        title = ((record.get("rows") or {}).get("title") or {}).get("val") or ""
        clean_title = re.sub(r"\s+", " ", re.sub(r"^\s*#+\s*", "", str(title))).strip()
        stem = path.stem
        session_id = stem if stem.startswith("session-") else f"session-{stem}"
        rows.append({"session_id": session_id, "cwd": cwd, "title": clean_title})
    return rows


def normalized(path: Path) -> str:
    return os.path.normpath(os.path.abspath(str(path)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workspace", default=os.getcwd(), help="workspace whose sessions to collect (default: cwd)")
    parser.add_argument("--out", default=str(default_out()), help="where to write the JSON")
    parser.add_argument("--apply-to", default="", help="optional tower.toml to patch with base_url and secret")
    args = parser.parse_args(argv)

    home = dsh_home()
    if not home.is_dir():
        print(f"no DSH home at {home} - is DeepSeek Harness installed for this user?", file=sys.stderr)
        return 1

    workspace = normalized(Path(args.workspace))
    root = Path(args.workspace).resolve()
    secret = find_secret(root)
    base_url = find_base_url(secret)
    sessions = session_index(home)

    matched = [row for row in sessions if normalized(Path(row["cwd"])) == workspace]
    if not matched:
        folded = workspace.casefold()
        matched = [row for row in sessions if folded in normalized(Path(row["cwd"])).casefold()]
    if not matched:
        print(f"no sessions found for {workspace}; listing every session instead", file=sys.stderr)
        matched = sessions

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "workspace": workspace,
        "base_url": base_url,
        "secret": secret,
        "session_count": len(matched),
        "sessions": [
            {"title": row["title"], "session_id": row["session_id"]}
            for row in sorted(matched, key=lambda row: row["title"])
        ],
    }

    if args.apply_to:
        target = Path(args.apply_to)
        if not target.is_file():
            print(f"ApplyTo target not found: {target}", file=sys.stderr)
        else:
            text = target.read_text(encoding="utf-8")
            if base_url:
                text = re.sub(r'(?m)^(\s*base_url:\s*)"[^"]*"', rf'\1"{base_url}"', text)
            if secret:
                text = re.sub(r'(?m)^(\s*secret:\s*)"[^"]*"', rf'\1"{secret}"', text)
            target.write_text(text, encoding="utf-8")
            print(f"applied base_url + secret to {target}")

    body = json.dumps(payload, indent=2, ensure_ascii=False)
    Path(args.out).write_text(body + "\n", encoding="utf-8")
    print(f"written: {args.out}\n")
    print(body)
    return 0 if matched else 2


if __name__ == "__main__":
    raise SystemExit(main())
