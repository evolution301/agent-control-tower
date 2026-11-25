"""One-shot machine wiring for a tower that drives live DSH sessions.

`tower bootstrap` does the whole manual checklist in one run:

1. find the desktop harness (scan listening ports, confirm with `session/list`),
2. recover the HMAC secret from an existing bridge script or the environment,
3. write `base_url` + `secret` into the tower config,
4. drop verify commands that cannot run on this machine,
5. resolve a session for every role (cache -> title match on the RPC row or the
   disk projection cache -> create), optionally pinning the ids in the config,
6. with `adapters.dsh.pool_size` set, reconcile that many spare sessions and
   register them, so a role added later is visible without a restart,
7. optionally send one real brief and wait for its report.

Every step is idempotent: running it twice changes nothing and costs one
`session/list`.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import time
from pathlib import Path

from . import sidebar
from .adapters.base import AdapterRequest
from .adapters.dsh import DshAdapter, DshRpcError
from .config import load_config

# Pre-registered sessions: DSH reads its sidebar registry once per boot, so a
# role that gets a session later is invisible until a restart. A pool of spare
# sessions, created AND registered by bootstrap, lets `role add` claim one and
# show up immediately. `size` counts the FREE spares; claimed ids are kept in
# the same record as an audit trail and never count toward capacity.
POOL_PREFIX = "tower pool"
POOL_EMPTY_WARNING = "pool empty — new roles need a restart until refilled"

SECRET_RE = re.compile(r'SECRET_B64\s*=\s*"([^"]+)"')
# A legal YAML value is quoted OR a bare scalar; the old `"[^"]*"`-only pattern
# missed `base_url: http://127.0.0.1:54250`, so the subn below matched nothing
# and the command still printed the detected value as written.
_YAML_VALUE = r'''(?:"[^"\n]*"|'[^'\n]*'|[^\s#]+)'''
BASE_URL_LINE = re.compile(r'(?m)^([ \t]*base_url:[ \t]*)' + _YAML_VALUE)
SECRET_LINE = re.compile(r'(?m)^([ \t]*secret:[ \t]*)' + _YAML_VALUE)
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
    try:
        reply = adapter.rpc("session/list", {"_request": {}}, timeout=timeout)
    except DshRpcError as exc:
        # A rejection is a *classification*, not a crash: 401/403 means the host
        # is there and the secret is wrong; anything else means "not a harness".
        payload = {"error": str(exc), "code": exc.code}
        if exc.code in ("http/401", "http/403"):
            return "needs-secret", payload
        return None, payload
    if not reply:
        # A web page that answers 200 with an empty body arrives here as `{}`
        # (the adapter decodes an empty body as an empty object). The tower's
        # own panel on :9012 does exactly that — it is not a harness.
        return None, {"error": "empty reply to session/list", "code": "gateway/empty"}
    return "harness", reply


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


CREDENTIALS_PATH = Path.home() / ".dsh" / ".credentials.yaml"


def _secret_from_credentials() -> str:
    """`~/.dsh/.credentials.yaml` -> records.<id>.payload.secret.

    This is where the desktop harness itself keeps the bridge secret — the one
    place a brand-new user (no bridge script yet) can actually find it.
    Read-only; never echoed into any config diff or log line.
    """
    try:
        import yaml

        data = yaml.safe_load(CREDENTIALS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return ""
    records = (data or {}).get("records") or {}
    if not isinstance(records, dict):
        return ""
    for record in records.values():
        payload = (record or {}).get("payload") or {}
        secret = str(payload.get("secret") or "").strip()
        if secret:
            return secret
    return ""


def detect_secret(workspace: Path) -> str:
    """base64 HMAC secret: env → an existing bridge script → the harness credentials file."""
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
    return _secret_from_credentials()


def secret_locations_checked(workspace: Path) -> list[str]:
    """Human-readable list of everywhere detect_secret looked (for the 401 message)."""
    env_set = "set" if os.environ.get("DSH_BRIDGE_SECRET", "").strip() else "(unset)"
    scripts = any(sorted(workspace.glob(pattern)) for pattern in SECRET_SOURCES)
    return [
        f"$DSH_BRIDGE_SECRET                              {env_set}",
        f'SECRET_B64 = "..." in *.py                      {"(some)" if scripts else "(none)"}',
        f"{CREDENTIALS_PATH}  "
        + ("(found)" if CREDENTIALS_PATH.exists() else "(not found)"),
    ]


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
    """Comment out verify commands that cannot run here. Returns the dropped ones.

    B5 (2026-11-22): never *replace* the user's checks with `echo ok` — that made
    the acceptance chain self-grading. Unrunnable commands are commented out in
    place; runnable ones survive untouched.
    """
    cfg = load_config(config_path.parent)
    broken = set(command for command in cfg.verify_commands if not command_available(command))
    if not broken:
        return []
    lines = config_path.read_text(encoding="utf-8").splitlines(keepends=True)
    start = None
    for i, line in enumerate(lines):
        if line.strip() == "verify_commands:":
            start = i
            break
    if start is None:
        return sorted(broken)
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].strip() and not lines[j][0].isspace():
            end = j
            break
    out: list[str] = [lines[start]]
    for line in lines[start + 1:end]:
        stripped = line.strip()
        if stripped.startswith("-"):
            item = stripped[1:].strip()
            command = item.strip("\"'`").strip()
            if command in broken:
                indent = line[:len(line) - len(line.lstrip())]
                out.append(f"{indent}# tower bootstrap: cannot run here: {item}\n")
                continue
        out.append(line)
    lines[start:end] = out
    config_path.write_text("".join(lines), encoding="utf-8")
    return sorted(broken)


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
    try:
        return adapter.rpc("session/delete", {"request": {"sessionId": session_id}})
    except DshRpcError as exc:
        return {"error": str(exc), "code": exc.code}


def pool_size(cfg) -> int:
    """`adapters.dsh.pool_size`; 0 (off) for every tower that never set it."""
    options = (cfg.adapter_options or {}).get("dsh") or {}
    try:
        return max(0, int(options.get("pool_size", 0) or 0))
    except (TypeError, ValueError):
        return 0


def pool_path(cfg) -> Path:
    return cfg.state_dir / "dsh_pool.json"


def _entry_id(entry) -> str:
    """A free/claimed entry is a session id or `{"session": id, …}` (both writers)."""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        return str(entry.get("session") or entry.get("id") or "")
    return ""


def _claimed_ids(claimed: dict) -> set[str]:
    return {sid for sid in (_entry_id(entry) for entry in claimed.values()) if sid}


def load_pool(cfg) -> dict | None:
    """The record, or None when it is missing or unreadable (bootstrap rebuilds it).

    Entries are kept verbatim so a rewrite by bootstrap cannot flatten the
    `{"session": …, "title": …}` shape `dsh pool list` writes.
    """
    try:
        raw = json.loads(pool_path(cfg).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    try:
        size = max(0, int(raw.get("size") or 0))
    except (TypeError, ValueError):
        size = 0
    claimed = (raw.get("claimed") or {}) if isinstance(raw.get("claimed"), dict) else {}
    return {
        "size": size,
        "free": [sid for sid in (_entry_id(e) for e in (raw.get("free") or [])) if sid],
        "claimed": {str(role): entry for role, entry in claimed.items() if _entry_id(entry)},
    }


def save_pool(cfg, record: dict) -> None:
    path = pool_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def pool_state(cfg) -> dict:
    """What `tower dsh pool list` prints. Never raises; `error` says the record is unusable."""
    record = load_pool(cfg)
    if record is not None:
        return record
    state = {"size": pool_size(cfg), "free": [], "claimed": {}}
    if pool_path(cfg).exists():
        state["error"] = f"{pool_path(cfg)} is unreadable — the next bootstrap rebuilds it"
    return state


def pool_warning(cfg) -> str:
    """The doctor line, or "" when the pool can still serve a new role."""
    if not pool_size(cfg):
        return ""
    record = load_pool(cfg) or {}
    return "" if record.get("free") else POOL_EMPTY_WARNING


def _registry_snapshot(cfg) -> tuple[set[str] | None, str]:
    """Registered session ids for this workspace, or (None, why not)."""
    data = sidebar.load_registry()
    if data is None:
        return None, (
            f"no DSH workspace registry at {sidebar.registry_path()} — "
            "leave pool_size at 0 on this machine"
        )
    found = sidebar.workspace_row(data, cfg.workspace)
    if not found:
        return None, (
            f"this tower's workspace is not in the DSH registry ({cfg.workspace}) — "
            "open it once in the desktop app, then bootstrap again"
        )
    return {str(s) for s in (found[1].get("sessionIds") or [])}, ""


def register_pool_ids(cfg, ids: list[str]) -> dict:
    """Append ids to the workspace row — the same file `dsh register --apply` writes."""
    data = sidebar.load_registry()
    found = sidebar.workspace_row(data, cfg.workspace) if data else None
    if found is None:
        return {"added": [], "error": "no registry row for this workspace"}
    path = sidebar.registry_path()
    sessions = [str(s) for s in (found[1].get("sessionIds") or [])]
    missing = [sid for sid in ids if sid not in set(sessions)]
    if not missing:
        return {"added": [], "registry": str(path)}
    backup = path.with_suffix(f".json.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(path, backup)
    found[1]["sessionIds"] = sessions + missing
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return {"added": missing, "backup": str(backup), "registry": str(path)}


def _session_rows(adapter) -> tuple[list[dict], str]:
    try:
        reply = adapter.rpc("session/list", {"_request": {}})
        value = DshAdapter._result_value(reply)
    except DshRpcError as exc:
        return [], f"session/list failed ({exc}) — nothing was created"
    items = (value or {}).get("items") if isinstance(value, dict) else None
    if not isinstance(items, list):
        return [], "session/list answered without items — nothing was created"
    return [item for item in items if isinstance(item, dict)], ""


def _pool_title(item: dict) -> str:
    projections = item.get("projections") or {}
    values = projections.get("values") if isinstance(projections, dict) else None
    return str((values or {}).get("title") or "") if isinstance(values, dict) else ""


def _pool_titles(rows: list[dict]) -> dict[str, str]:
    return {
        str(item.get("sessionId") or ""): _pool_title(item)
        for item in rows
        if item.get("sessionId")
    }


def _adopt_pooled(rows: list[dict], known: set[str], registered: set[str] | None, size: int) -> list[str]:
    """Recover pooled ids from the harness when the record is gone or corrupt.

    A pooled session is identified by its title prefix and confirmed by the
    registry, so a lost `.tower/dsh_pool.json` costs a diff, not N new sessions.
    """
    adopted: list[str] = []
    for item in rows:
        sid = str(item.get("sessionId") or "")
        if not sid or sid in known or len(adopted) >= size:
            continue
        if not _pool_title(item).startswith(POOL_PREFIX):
            continue
        if registered is not None and sid not in registered:
            continue
        adopted.append(sid)
    return adopted


def reconcile_pool(cfg, adapter) -> dict:
    """Create the missing spares, register every free one, save the record.

    Idempotent by reconciliation, never by append: ids already in the harness
    are kept, ids the harness no longer has are replaced, and a missing record
    is rebuilt from the registry instead of duplicating the pool.
    """
    size = pool_size(cfg)
    result = {"size": size, "free": [], "claimed": {}, "created": [], "registered": [], "error": ""}
    if size <= 0:
        return {**result, "skipped": "adapters.dsh.pool_size is 0"}

    loaded = load_pool(cfg)
    record = loaded or {"size": size, "free": [], "claimed": {}}
    rows, error = _session_rows(adapter)
    if error:
        return {**result, "free": record["free"], "claimed": record["claimed"], "error": error}

    titles = _pool_titles(rows)
    live = set(titles)
    if loaded is None:
        # No usable bookkeeping: rebuild `claimed` from the role sessions that
        # carry a pool title, then recover the free ones from the registry diff.
        record["claimed"] = {
            str(role): str(sid)
            for role, sid in (getattr(adapter, "sessions", None) or {}).items()
            if sid in live and titles.get(str(sid), "").startswith(POOL_PREFIX)
        }
    claimed_ids = _claimed_ids(record["claimed"])
    free = [sid for sid in record["free"] if sid in live and sid not in claimed_ids]

    registered, error = _registry_snapshot(cfg)
    if loaded is None:
        free = _adopt_pooled(rows, set(free) | claimed_ids, registered, size)

    created: list[str] = []
    added: dict = {"added": []}
    if not error:
        for _ in range(max(0, size - len(free))):
            session_id = adapter.create_session(f"{POOL_PREFIX} {secrets.token_hex(3)}", str(cfg.workspace))
            if not session_id:
                error = "the harness refused to create a session — the pool is short"
                break
            created.append(session_id)
            free.append(session_id)
        if free:
            added = register_pool_ids(cfg, free)
            error = added.get("error", "")

    save_pool(cfg, {"size": size, "free": free, "claimed": record["claimed"]})
    return {
        "size": size,
        "free": free,
        "claimed": record["claimed"],
        "created": created,
        "registered": added.get("added", []),
        "error": error,
    }


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
    if needs_secret and not secret:
        # B2 (2026-11-22): the harness answered 401 and we have nothing to send.
        # "Bootstrap succeeded" and "nothing is wired" must not look the same.
        checked = secret_locations_checked(workspace)
        summary["error"] = (
            "the harness rejected the connection (401) and no secret was found - "
            "nothing was wired. The harness stores it at "
            "~/.dsh/.credentials.yaml -> records.<id>.payload.secret, or set "
            "DSH_BRIDGE_SECRET. Checked:\n  " + "\n  ".join(checked))
        summary["secret_checked"] = checked
        return summary

    changed = apply_connection(config_path, base_url=detected, secret=secret)
    summary["steps"].append({"step": "config", "changed": changed})
    if "base_url" not in changed:
        # 0.18.0 printed the detected value as written while the file still held
        # the old one. Nothing was rewritten: refuse instead of reporting a
        # connection that is not there, and name the line a person must fix.
        raise ValueError(
            f"could not write base_url {detected} — edit tower.toml by hand"
        )

    dropped = fix_verify_commands(config_path)
    summary["steps"].append({"step": "verify_commands", "dropped": dropped})

    rows, adapter = resolve_roles(config_path, secret=secret, base_url=detected, pin=pin)
    summary["roles"] = rows
    summary["pinned"] = pin
    summary["steps"].append({"step": "sessions", "resolved": sum(1 for r in rows if r["session_id"])})

    if pool_size(cfg):
        # A role added later can only be seen without a restart if its session is
        # registered before boot — hence spare sessions created and registered here.
        pool = reconcile_pool(cfg, adapter)
        summary["pool"] = pool
        summary["steps"].append({
            "step": "pool",
            "size": pool["size"],
            "free": len(pool["free"]),
            "created": len(pool["created"]),
            "error": pool.get("error", ""),
        })

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
