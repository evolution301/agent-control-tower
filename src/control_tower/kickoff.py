"""Kickoff: agree before dispatching (v3 §3).

The live tower burned three dispatch rounds and four hours on one page because
nobody had agreed on the diagnosis, the split, or the pass mark before the first
bot started. A kickoff produces that agreement as a file, not as chat:

    task package = plan / assignments / acceptance / deps / merge_order

`tower kickoff` opens a group, writes `.tower/groups/<G>/package.json` plus a
`minutes.md`, optionally runs one huddle round, and only then are tasks split out
of it. Each dispatch carries a compact package section; the full package stays
on disk, so a bot reads it once and later tasks reference it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .board import Board, now_ts
from .config import TowerConfig
from . import groups as groups_mod

SECTIONS = ("plan", "assignments", "acceptance", "deps", "merge_order")


def package_path(cfg: TowerConfig, group_id: str) -> Path:
    return groups_mod.group_dir(cfg, group_id) / "package.json"


def _next_package_id(cfg: TowerConfig) -> str:
    numbers = []
    for path in cfg.groups_dir.glob("G-*/package.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        pid = str(data.get("id", ""))
        if pid.startswith("P-") and pid[2:].isdigit():
            numbers.append(int(pid[2:]))
    return f"P-{max(numbers, default=0) + 1:04d}"


def template(cfg: TowerConfig, group_id: str, title: str, brief: str, roles: list[str]) -> dict[str, Any]:
    """An empty package with the roles already seated.

    Empty sections are deliberate: the kickoff is the meeting that fills them,
    and `validate` refuses to let a dispatch pretend otherwise.
    """
    package_id = _next_package_id(cfg)
    return {
        "id": package_id,
        "group": group_id,
        "title": title,
        "brief": brief.strip(),
        "roles": list(roles),
        "plan": {"best": "", "alternatives": [], "why": ""},
        "assignments": [{"role": role, "work": "", "scope": []} for role in roles],
        "acceptance": {"commands": [], "thresholds": []},
        "deps": [],
        "merge_order": list(roles),
        "created_at": now_ts(),
        "draft": True,
    }


def save_package(cfg: TowerConfig, group_id: str, package: dict[str, Any]) -> Path:
    path = package_path(cfg, group_id)
    path.write_text(json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def load_package(cfg: TowerConfig, group_id: str) -> dict[str, Any] | None:
    path = package_path(cfg, group_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def missing(package: dict[str, Any]) -> list[str]:
    """Which of the four agreements are still blanks (for the error message)."""
    out: list[str] = []
    plan = package.get("plan") or {}
    if not str(plan.get("best") or "").strip():
        out.append("plan.best — what is the best fix, and why this one")
    assignments = [a for a in package.get("assignments") or [] if str(a.get("work") or "").strip()]
    if not assignments:
        out.append("assignments[].work — who does what, in which files")
    acceptance = package.get("acceptance") or {}
    if not (acceptance.get("commands") or acceptance.get("thresholds")):
        out.append("acceptance — the command(s) and the threshold that decide pass")
    if not package.get("merge_order"):
        out.append("merge_order — who lands first, so parallel work does not collide")
    return out


def validate(package: dict[str, Any] | None) -> list[str]:
    if not package:
        return ["no task package: run `tower kickoff` first (plan / assignments / acceptance / merge order)"]
    return missing(package)


def kickoff(
    cfg: TowerConfig,
    board: Board,
    brief: str,
    roles: list[str],
    title: str = "",
    discuss: bool = False,
    rounds: int = 1,
) -> dict[str, Any]:
    """Open a group and write its task package. Returns {group, package, path, minutes, problems}."""
    clean = [role for role in dict.fromkeys(roles) if role]
    unknown = [role for role in clean if role not in cfg.roles]
    if unknown:
        from .config import unknown_role_message

        raise KeyError(unknown_role_message(cfg, unknown[0]))
    if not clean:
        raise ValueError("a kickoff needs at least one role")
    if not (brief or "").strip():
        raise ValueError("a kickoff needs a brief")

    group = groups_mod.create(cfg, title or brief.strip().splitlines()[0][:80], clean)
    package = template(cfg, group.id, group.title, brief, clean)
    path = save_package(cfg, group.id, package)
    group.package = package["id"]
    group.status = "discussing"
    groups_mod.save(cfg, group)

    minutes = group_dir_minutes = groups_mod.group_dir(cfg, group.id) / "minutes.md"
    body = [
        f"# {group.id} {group.title}",
        "",
        f"- package: {package['id']}",
        f"- roles: {', '.join(clean)}",
        f"- opened: {group.created_at}",
        "",
        "## Brief",
        "",
        brief.strip(),
        "",
        "## What the package must answer",
        "",
        "1. plan — best fix, alternatives, why",
        "2. assignments — who owns which files (scope)",
        "3. acceptance — the command and the threshold",
        "4. deps / merge order — who lands first",
        "",
    ]
    minutes.write_text("\n".join(body), encoding="utf-8")

    problems = missing(package)
    discussed = ""
    if discuss and len(clean) >= 2:
        from .huddle import huddle

        record = huddle(cfg, board, clean, topic=brief.strip(), rounds=max(1, min(2, rounds)))
        discussed = record.get("path", "")
        package["minutes"] = discussed
        package["huddle"] = record.get("id", "")
        save_package(cfg, group.id, package)

    board.log("kickoff", group=group.id, package=package["id"], roles=clean, problems=len(problems),
              discussed=bool(discussed))
    return {
        "group": group.to_dict(),
        "package": package,
        "path": str(path),
        "minutes": discussed or str(minutes),
        "problems": problems,
    }


def attach_task(cfg: TowerConfig, group_id: str, task_id: str) -> None:
    groups_mod.attach(cfg, group_id, task_id)


def package_section(cfg: TowerConfig, group_id: str, role: str = "", limit: int = 1400) -> str:
    """The compact block a dispatched prompt carries instead of the whole package."""
    package = load_package(cfg, group_id)
    if not package:
        return ""
    plan = package.get("plan") or {}
    acceptance = package.get("acceptance") or {}
    mine = next((a for a in package.get("assignments") or [] if a.get("role") == role), None)
    lines = [
        f"## Task package {package.get('id')} (group {group_id})",
        f"- plan: {str(plan.get('best') or '—').strip()[:300]}",
    ]
    if mine:
        lines.append(
            f"- your part: {str(mine.get('work') or '—').strip()[:240]}"
            f" (scope: {', '.join(mine.get('scope') or []) or 'not set'})"
        )
    if acceptance.get("commands"):
        lines.append(f"- acceptance: {'; '.join(acceptance['commands'])[:300]}")
    if acceptance.get("thresholds"):
        lines.append(f"- thresholds: {'; '.join(str(t) for t in acceptance['thresholds'])[:200]}")
    if package.get("merge_order"):
        lines.append(f"- merge order: {' → '.join(package['merge_order'])}")
    lines.append(f"- full package: {package_path(cfg, group_id)}")
    text = "\n".join(lines)
    return text[:limit]
