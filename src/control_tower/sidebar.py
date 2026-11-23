"""DSH sidebar registration (v3 §2).

A bot is only visible in the DSH sidebar when its session id sits in the
workspace registry that the desktop app reads at boot:

    $DSH_HOME/storages/workspace.json   (tables.workspaces[<id>].sessionIds)

The tower has created sessions for a year without ever adding them there, which
is why every new bot needed a manual edit plus a restart. This module reads the
registry, says which roles are missing, and — with `apply` — adds them after
taking a backup. The app only re-reads it at boot, so a restart is still the last
step; what changes is that nobody has to find the file by hand.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from .config import TowerConfig


def dsh_home() -> Path:
    raw = os.environ.get("DSH_HOME", "")
    return Path(raw).expanduser() if raw else Path.home() / ".dsh"


def registry_path() -> Path:
    return dsh_home() / "storages" / "workspace.json"


def _norm(path: str | Path) -> str:
    return os.path.normcase(str(path).replace("\\", "/")).rstrip("/")


def role_sessions(cfg: TowerConfig) -> dict[str, str]:
    dsh = (cfg.adapter_options or {}).get("dsh") or {}
    sessions = dsh.get("sessions") or {}
    return {str(role): str(session) for role, session in sessions.items() if session}


def load_registry() -> dict[str, Any] | None:
    path = registry_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def workspace_row(data: dict[str, Any], workspace: str | Path) -> tuple[str, dict[str, Any]] | None:
    tables = (data or {}).get("tables") or {}
    rows = tables.get("workspaces") or {}
    target = _norm(workspace)
    for workspace_id, row in rows.items():
        if _norm(row.get("path", "")) == target:
            return str(workspace_id), row
    return None


def status(cfg: TowerConfig) -> dict[str, Any]:
    data = load_registry()
    sessions = role_sessions(cfg)
    if data is None:
        return {
            "registry": str(registry_path()),
            "registry_found": False,
            "workspace_found": False,
            "roles": [
                {"role": role, "session": sessions.get(role, ""), "registered": False,
                 "wired": bool(sessions.get(role))}
                for role in cfg.roles
            ],
        }
    found = workspace_row(data, cfg.workspace)
    registered: set[str] = set()
    if found:
        registered = {str(item) for item in (found[1].get("sessionIds") or [])}
    return {
        "registry": str(registry_path()),
        "registry_found": True,
        "workspace_found": bool(found),
        "workspace_id": found[0] if found else "",
        "roles": [
            {"role": role, "session": sessions.get(role, ""),
             "registered": bool(sessions.get(role)) and sessions.get(role) in registered,
             "wired": bool(sessions.get(role))}
            for role in cfg.roles
        ],
    }


def register(cfg: TowerConfig, apply: bool = False) -> dict[str, Any]:
    """Add every known session to the workspace row. Never invents a workspace."""
    data = load_registry()
    result: dict[str, Any] = {
        "registry": str(registry_path()),
        "registry_found": data is not None,
        "workspace_found": False,
        "missing": [],
        "added": [],
        "backup": "",
    }
    if data is None:
        result["error"] = "no DSH workspace registry found — is DSH installed on this machine?"
        return result
    found = workspace_row(data, cfg.workspace)
    if not found:
        result["error"] = (
            f"this tower's workspace is not registered with DSH yet ({cfg.workspace}) — "
            "open it once in the desktop app, then run this again"
        )
        return result
    workspace_id, row = found
    result["workspace_found"] = True
    result["workspace_id"] = workspace_id
    sessions = list(row.get("sessionIds") or [])
    known = set(sessions)
    missing = sorted({session for session in role_sessions(cfg).values() if session and session not in known})
    result["missing"] = missing
    if apply and missing:
        path = registry_path()
        backup = path.with_suffix(f".json.bak-{__import__('time').strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(path, backup)
        result["backup"] = str(backup)
        row["sessionIds"] = sessions + missing
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        result["added"] = missing
    result["restart_required"] = True
    return result


def restart_hint() -> str:
    return (
        "restart the desktop app for the sidebar to show the new bots "
        "(the registry is read once at boot); nothing else is needed"
    )
