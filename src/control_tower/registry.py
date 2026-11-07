"""Project registry: many towers, one place that knows about them.

A tower stays where it belongs — `<project>/.tower/` next to `tower.toml` — so a
project is still one directory you can copy, diff or delete. This module only
remembers *where the projects are* and *which one is active*, which is what the
panel needs to show a switcher and what the DSH bundle needs so `tower_*` tools
follow the project you are actually working in.

Files (override the directory with `TOWER_HOME`):

* `~/.tower/projects.json` — `[{"name", "path", "color"}]`, ordered.
* `~/.tower/active`        — one line, the path of the active project.

Discovery is deliberately shallow and boring: look for `tower.toml` under the
given roots, skip `node_modules`, `.tower`, `roles` and dot-directories. A project
nobody registered is not on the board — the collector's rule from the HQ
methodology.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

CONFIG_NAMES = ("tower.toml", "tower.yaml", "tower.yml", "tower.json")
SKIP_DIRS = {"node_modules", ".tower", "roles", ".git", "__pycache__", "venv", ".venv"}

# the HQ palette: a colour is an identity, never an alarm
PALETTE = ["#2563eb", "#db2777", "#ea7a0c", "#16a34a", "#0d9488", "#475569",
           "#7c3aed", "#0891b2", "#c2410c", "#be123c"]


def home_dir() -> Path:
    return Path(os.environ.get("TOWER_HOME") or (Path.home() / ".tower")).expanduser()


def registry_path() -> Path:
    return home_dir() / "projects.json"


def active_path() -> Path:
    return home_dir() / "active"


def binding_path() -> Path:
    """The tower this machine last really used — the aim that survives an update.

    A plugin update replaces code, never data; but a lost/blank `workspace` line in
    the profile config used to re-aim the remote at whatever demo tower happened to
    be around. This record is what makes the reconnection seamless: when the config
    no longer names a tower, the bundle falls back to the bound one instead of cwd.
    """
    return home_dir() / "binding.json"


# --------------------------------------------------------------------- storage


def load_projects() -> list[dict[str, Any]]:
    path = registry_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    projects = data.get("projects") if isinstance(data, dict) else data
    out = []
    for index, item in enumerate(projects or []):
        if not isinstance(item, dict) or not item.get("path"):
            continue
        out.append({
            "name": str(item.get("name") or Path(item["path"]).name),
            "path": str(Path(item["path"])),
            "color": item.get("color") or PALETTE[index % len(PALETTE)],
        })
    return out


def save_projects(projects: list[dict[str, Any]]) -> None:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"projects": projects}, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def config_in(directory: Path) -> Path | None:
    for name in CONFIG_NAMES:
        candidate = Path(directory) / name
        if candidate.exists():
            return candidate
    return None


def discover(roots: list[str | Path] | None = None, depth: int = 3) -> list[Path]:
    """Directories under `roots` (default: home) that hold a tower config."""
    found: list[Path] = []
    seen: set[str] = set()

    def walk(directory: Path, level: int) -> None:
        if level > depth:
            return
        try:
            entries = sorted(directory.iterdir())
        except (OSError, PermissionError):
            return
        for entry in entries:
            if not entry.is_dir() or entry.name in SKIP_DIRS or entry.name.startswith("."):
                continue
            if config_in(entry) is not None:
                resolved = str(entry.resolve())
                if resolved not in seen:
                    seen.add(resolved)
                    found.append(entry.resolve())
                continue
            walk(entry, level + 1)

    for root in roots or [Path.home()]:
        walk(Path(root).expanduser(), 0)
    return found


def project_name(directory: Path) -> str:
    config = config_in(directory)
    if config is not None:
        try:
            if config.suffix == ".json":
                data = json.loads(config.read_text(encoding="utf-8"))
                if data.get("name"):
                    return str(data["name"])
            else:
                import yaml

                data = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
                if data.get("name"):
                    return str(data["name"])
        except Exception:
            pass
    return Path(directory).name


# ------------------------------------------------------------------- mutations


def add_project(path: str | Path, name: str = "", color: str = "") -> dict[str, Any]:
    target = Path(path).expanduser().resolve()
    if config_in(target) is None:
        raise ValueError(f"{target} has no tower.toml — run `tower init` there first")
    projects = load_projects()
    for item in projects:
        if Path(item["path"]).resolve() == target:
            if name:
                item["name"] = name
            if color:
                item["color"] = color
            save_projects(projects)
            return item
    entry = {
        "name": name or project_name(target),
        "path": str(target),
        "color": color or PALETTE[len(projects) % len(PALETTE)],
    }
    projects.append(entry)
    save_projects(projects)
    return entry


def remove_project(name_or_path: str) -> dict[str, Any]:
    projects = load_projects()
    keep, dropped = [], None
    for item in projects:
        matches = (item["name"] == name_or_path or
                   str(Path(item["path"]).resolve()) == str(Path(name_or_path).expanduser().resolve()))
        if matches and dropped is None:
            dropped = item
        else:
            keep.append(item)
    if dropped is None:
        raise KeyError(f"unknown project {name_or_path!r}")
    save_projects(keep)
    if get_active() and get_active().get("name") == dropped["name"]:
        clear_active()
    return dropped


def resolve(name_or_path: str | None = None) -> Path:
    """A registered name, an explicit path, or the active project."""
    if name_or_path:
        for item in load_projects():
            if item["name"] == name_or_path:
                return Path(item["path"])
        candidate = Path(name_or_path).expanduser()
        if candidate.exists():
            return candidate.resolve()
        raise KeyError(f"unknown project {name_or_path!r}")
    active = get_active()
    if active:
        return Path(active["path"])
    raise KeyError("no active project — `tower projects use <name>` or pass a path")


def set_active(name_or_path: str) -> dict[str, Any]:
    path = resolve(name_or_path)
    entry = next((item for item in load_projects()
                  if Path(item["path"]).resolve() == path), None)
    if entry is None:
        entry = add_project(path)
    active_path().parent.mkdir(parents=True, exist_ok=True)
    active_path().write_text(json.dumps({"name": entry["name"], "path": entry["path"],
                                        "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
                                       ensure_ascii=False), encoding="utf-8")
    # switching the active project also binds it: that is the tower a later plugin
    # update must reconnect to if the profile config loses its workspace line
    record_binding(entry["path"], source="projects-use")
    return entry


def get_active() -> dict[str, Any] | None:
    path = active_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not data.get("path") or not Path(data["path"]).exists():
        return None
    return data


def clear_active() -> None:
    try:
        active_path().unlink()
    except OSError:
        pass


# ------------------------------------------------------------------- the aim


def has_tower(path: str | Path | None) -> bool:
    """True when the directory really holds a tower config."""
    return bool(path) and config_in(Path(path)) is not None


def read_binding() -> dict[str, Any] | None:
    try:
        data = json.loads(binding_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    path = data.get("path") if isinstance(data, dict) else None
    if not path or not has_tower(path):
        return None
    return {"path": str(Path(path)), "recorded_at": str(data.get("recorded_at", "")),
            "source": str(data.get("source", ""))}


def clear_binding() -> None:
    """Forget the bound tower (tests, or an intentional clean slate)."""
    try:
        binding_path().unlink()
    except OSError:
        pass


def record_binding(path: str | Path, source: str = "") -> dict[str, Any] | None:
    """Remember the tower that was actually used. No-op when nothing changes."""
    target = Path(path).resolve()
    if not has_tower(target):
        return None
    current = read_binding()
    if current and Path(current["path"]).resolve() == target:
        return current
    payload = {"path": str(target), "source": source,
               "recorded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    binding_path().parent.mkdir(parents=True, exist_ok=True)
    binding_path().write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"path": payload["path"], "recorded_at": payload["recorded_at"], "source": source}


def effective_workspace(configured: str | Path | None = None) -> Path | None:
    """Which tower should the tools point at? The answer to "don't lose my tower".

    Precedence, and *why*:

    1. the workspace named in the profile config — an explicit aim always wins, so a
       plugin update can never silently re-point a working installation;
    2. the bound tower — the seamless reconnection when that config line is missing
       or stale after an update;
    3. the active project from `tower projects use` — the switcher's default;
    4. nothing: the caller decides (cwd, or an actionable error).
    """
    if has_tower(configured):
        return Path(configured).resolve()
    bound = read_binding()
    if bound:
        return Path(bound["path"])
    active = get_active()
    if active:
        return Path(active["path"])
    return None


# ------------------------------------------------------------------- summaries


def summarise(entry: dict[str, Any]) -> dict[str, Any]:
    """Cheap per-project numbers for the switcher (no full snapshot build)."""
    from .board import Board, OPEN_STATES
    from .config import load_config

    directory = Path(entry["path"])
    row = {"name": entry["name"], "path": entry["path"], "color": entry["color"],
           "ok": False, "tasks": 0, "open": 0, "needs_you": 0, "overdue": 0,
           "bots": 0, "working": 0, "error": ""}
    try:
        cfg = load_config(directory)
        board = Board(cfg)
        tasks = board.all()
    except Exception as exc:  # a broken project must not take the board down
        row["error"] = f"{type(exc).__name__}: {exc}"[:200]
        return row
    row.update({
        "ok": True,
        "name": entry["name"] or cfg.name,
        "bots": len(cfg.roles),
        "tasks": len(tasks),
        "accepted": sum(1 for t in tasks if t.status == "accepted"),
        "open": sum(1 for t in tasks if t.status in OPEN_STATES),
        "needs_you": len(board.needs_person()),
        "overdue": sum(1 for t in tasks if t.overdue),
        "working": sum(1 for t in tasks if t.status in ("claimed", "in_progress")),
    })
    return row


def overview(projects: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """The aggregate board: every registered project, plus the sums."""
    entries = projects if projects is not None else load_projects()
    rows = [summarise(item) for item in entries]
    active = get_active()
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "active": active["name"] if active else "",
        "projects": rows,
        "totals": {
            "projects": len(rows),
            "bots": sum(r["bots"] for r in rows),
            "tasks": sum(r["tasks"] for r in rows),
            "accepted": sum(r.get("accepted", 0) for r in rows),
            "open": sum(r["open"] for r in rows),
            "needs_you": sum(r["needs_you"] for r in rows),
            "overdue": sum(r["overdue"] for r in rows),
            "working": sum(r["working"] for r in rows),
            "broken": sum(1 for r in rows if not r["ok"]),
        },
    }
