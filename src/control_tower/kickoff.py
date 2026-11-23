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
import re
from pathlib import Path
from typing import Any

from .board import Board, now_ts
from .config import TowerConfig
from . import groups as groups_mod

SECTIONS = ("plan", "assignments", "acceptance", "deps", "merge_order")

_PATH = re.compile(
    r"[A-Za-z0-9_.@/\\-]*(?:[/\\][A-Za-z0-9_.@/\\-]+"
    r"|[A-Za-z0-9_@-]+\.(?:py|md|json|toml|ya?ml|css|js|ts|tsx|html|sh|txt|ini|cfg))\b"
)
_COMMAND = re.compile(
    r"\b(?:pytest|python[0-9.]*|npm|pnpm|yarn|node|cargo|go\s+test|make|curl|bash|sh"
    r"|ruff|mypy|black|eslint|tsc)\b",
    re.I,
)
_THRESHOLD = re.compile(
    r"(?:[<>]=?|[≤≥]|at least|no more than|under|below|green|pass(?:es|ing)?"
    r"|failures?|%|\d+(?:\.\d+)?\s*(?:s|ms|sec|seconds?|tests?))\b",
    re.I,
)


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


def _sections(text: str) -> dict[str, list[str]]:
    """`## Heading` → body lines. Same rule as dispatch._sections, kept local
    because dispatch imports this module at import time."""
    out: dict[str, list[str]] = {}
    key = ""
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if stripped.startswith("## "):
            key = stripped[3:].strip().lower()
            out.setdefault(key, [])
        elif key:
            out[key].append(line)
    return out


def _bullet(line: str) -> str:
    stripped = line.strip()
    return stripped[1:].strip() if stripped[:1] in ("-", "*", "•") else stripped


def _section(blocks: dict[str, list[str]], *names: str) -> list[str] | None:
    for name in names:
        if name in blocks:
            return blocks[name]
    return None


def _paths(text: str) -> list[str]:
    out: list[str] = []
    for hit in _PATH.findall(text):
        hit = hit.strip(".,;:()[]")
        if hit and hit not in out:
            out.append(hit)
    return out


def _parse_plan(lines: list[str]) -> dict[str, Any]:
    best = why = ""
    alternatives: list[str] = []
    paragraphs: list[str] = []
    mode = ""
    for raw in lines:
        line = _bullet(raw)
        if not line:
            continue
        label = re.match(r"^(?:\*\*)?(best|why|alternatives?)(?:\*\*)?\s*[:\-—]\s*(.*)$", line, re.I)
        if label:
            key = label.group(1).lower()
            value = label.group(2).strip()
            if key == "best":
                best = value
            elif key == "why":
                why = value
            else:
                alternatives = [part.strip() for part in re.split(r"[;|]", value) if part.strip()]
            mode = key
            continue
        if mode.startswith("alternative"):
            alternatives.append(line)
        elif mode == "why":
            why = f"{why} {line}".strip()
        elif mode == "best":
            best = f"{best} {line}".strip()
        elif not best:
            best = line
        else:
            paragraphs.append(line)
    if not best and paragraphs:
        best = paragraphs.pop(0)
    if not why:
        why = next((p for p in paragraphs if p.lower().startswith(("because ", "since "))), "")
    if not alternatives:
        alternatives = [p for p in paragraphs if p != why]
    return {"best": best, "alternatives": alternatives, "why": why}


def _reason_split(text: str) -> tuple[str, str]:
    hit = re.search(r"\s+(?:because|since)\s+", text, re.I)
    if not hit:
        return text.strip(), ""
    return text[: hit.start()].strip(" .;,"), text[hit.end():].strip()


def _role_work(line: str, roles: list[str]) -> tuple[str, str]:
    plain = _bullet(line).replace("**", "")
    for role in sorted(roles, key=len, reverse=True):
        if not plain.lower().startswith(role.lower()):
            continue
        rest = plain[len(role):]
        if rest[:1] in (":", "—", "–", "-"):
            return role, rest[1:].strip(" -—:")
        if rest[:2] == " -":
            return role, rest[2:].strip()
    return "", ""


def _split_scope(work: str) -> tuple[str, list[str]]:
    hit = re.search(r"\(?\s*scope\s*:\s*([^)]+)\)?", work, re.I)
    if hit:
        scope = [part.strip() for part in re.split(r"[,;]", hit.group(1)) if part.strip()]
        work = (work[: hit.start()] + work[hit.end():]).strip(" ,;.-")
        return work, scope
    return work, _paths(work)


def _parse_acceptance(lines: list[str]) -> tuple[list[str], list[str]]:
    commands: list[str] = []
    thresholds: list[str] = []
    mode = ""
    for raw in lines:
        line = _bullet(raw)
        if not line:
            continue
        label = re.match(r"^(?:\*\*)?(commands?|thresholds?)(?:\*\*)?\s*[:\-—]\s*(.*)$", line, re.I)
        if label:
            mode = "commands" if label.group(1).lower().startswith("command") else "thresholds"
            value = label.group(2).strip()
            if value:
                (commands if mode == "commands" else thresholds).append(value)
            continue
        if mode == "commands":
            commands.append(line)
        elif mode == "thresholds":
            thresholds.append(line)
        elif _THRESHOLD.search(line):
            thresholds.append(line)
        elif _COMMAND.search(line) or line.startswith(("`", "$", "> ")):
            commands.append(line)
        else:
            thresholds.append(line)
    return commands, thresholds


def _parse_merge(lines: list[str], roles: list[str]) -> list[str]:
    text = " ".join(_bullet(line) for line in lines if _bullet(line))
    text = re.sub(r"(?i)^\s*merge[_ -]?order\s*[:\-—]\s*", "", text)
    by_lower = {role.lower(): role for role in roles}
    order: list[str] = []
    for token in re.split(r"\s*(?:→|->|=>|,|;|\|)\s*|\s+", text):
        role = by_lower.get(token.strip(" .").lower())
        if role and role not in order:
            order.append(role)
    return order


def _huddle_turns(text: str) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    round_no = 0
    turn: dict[str, Any] | None = None
    mode = ""
    for raw in text.splitlines():
        line = raw.strip()
        hit = re.match(r"^##\s+Round\s+(\d+)\b", line, re.I)
        if hit:
            round_no = int(hit.group(1))
            turn, mode = None, ""
            continue
        if round_no and line.startswith("### "):
            role = line[4:].strip().split()[0].strip("*")
            turn = {"round": round_no, "role": role, "decision": [], "position": [], "commitments": []}
            turns.append(turn)
            mode = ""
            continue
        if turn is None:
            continue
        if line.startswith("**Decision**"):
            turn["decision"].append(line[len("**Decision**"):].strip(" —-"))
            mode = "decision"
        elif line.startswith("**Position**"):
            turn["position"].append(line[len("**Position**"):].strip(" —-"))
            mode = "position"
        elif line.startswith("**Commitments**"):
            mode = "commitments"
        elif line.startswith("**"):
            mode = ""
        elif mode == "commitments":
            item = _bullet(line)
            if item and item.lower() not in ("none", "n/a"):
                turn["commitments"].append(item)
        elif mode in ("decision", "position") and line:
            turn[mode].append(line)
    for turn in turns:
        turn["decision"] = " ".join(turn["decision"]).strip()
        turn["position"] = " ".join(turn["position"]).strip()
    return turns


def _fill_from_turns(package: dict[str, Any], text: str, filled: list[str]) -> None:
    """Fallback for a raw huddle record: round-2 decisions and commitments."""
    turns = _huddle_turns(text)
    decisions = [t for t in turns if t["round"] >= 2 and t["decision"]]
    plan = package.setdefault("plan", {})
    if not str(plan.get("best") or "").strip():
        source = decisions[0]["decision"] if decisions else next(
            (t["position"] for t in turns if t["round"] == 1 and t["position"]), ""
        )
        if source:
            best, why = _reason_split(source)
            if best:
                plan["best"] = best
                filled.append("plan.best")
            if why and not str(plan.get("why") or "").strip():
                plan["why"] = why
                filled.append("plan.why")
    by_role = {str(a.get("role")): a for a in package.get("assignments") or []}
    for turn in turns:
        if turn["round"] < 2:
            continue
        item = by_role.get(turn["role"])
        if item is None:
            continue
        commitment = "; ".join(turn["commitments"])
        if str(item.get("work") or "").strip():
            paths = _paths(commitment)
        else:
            work, paths = _split_scope(commitment)
            if work:
                item["work"] = work
                filled.append(f"assignments.{turn['role']}.work")
        if paths and not item.get("scope"):
            item["scope"] = paths
            filled.append(f"assignments.{turn['role']}.scope")
    acceptance = package.setdefault("acceptance", {})
    if not (acceptance.get("commands") or acceptance.get("thresholds")):
        candidates = [
            item for turn in turns for item in turn["commitments"]
            if _COMMAND.search(item) or _THRESHOLD.search(item)
        ]
        commands, thresholds = _parse_acceptance(candidates)
        if commands:
            acceptance["commands"] = commands
            filled.append("acceptance.commands")
        if thresholds:
            acceptance["thresholds"] = thresholds
            filled.append("acceptance.thresholds")


def fill_from_minutes(package: dict[str, Any], minutes_text: str) -> list[str]:
    """Fill the empty package fields from a huddle transcript; return what changed.

    Understands the explicit four-section transcript (`## Plan`, `## Assignments`,
    `## Acceptance`, `## Merge order`) and, as a fallback, the round-2 decisions
    and commitments that `huddle.render_markdown` writes. A field the transcript
    does not answer stays empty.
    """
    filled: list[str] = []
    text = minutes_text or ""
    blocks = _sections(text)

    plan_lines = _section(blocks, "plan", "approach", "fix")
    if plan_lines is not None:
        parsed = _parse_plan(plan_lines)
        plan = package.setdefault("plan", {})
        if parsed["best"] and not str(plan.get("best") or "").strip():
            plan["best"] = parsed["best"]
            filled.append("plan.best")
        if parsed["alternatives"] and not plan.get("alternatives"):
            plan["alternatives"] = parsed["alternatives"]
            filled.append("plan.alternatives")
        if parsed["why"] and not str(plan.get("why") or "").strip():
            plan["why"] = parsed["why"]
            filled.append("plan.why")

    assignment_lines = _section(blocks, "assignments", "assignment", "work split", "split")
    if assignment_lines is not None:
        by_role = {str(a.get("role")): a for a in package.get("assignments") or []}
        for raw in assignment_lines:
            role, work = _role_work(raw, list(by_role))
            if not role or not work:
                continue
            item = by_role[role]
            work, scope = _split_scope(work)
            if work and not str(item.get("work") or "").strip():
                item["work"] = work
                filled.append(f"assignments.{role}.work")
            if scope and not item.get("scope"):
                item["scope"] = scope
                filled.append(f"assignments.{role}.scope")

    acceptance_lines = _section(blocks, "acceptance", "acceptance criteria", "pass mark")
    if acceptance_lines is not None:
        commands, thresholds = _parse_acceptance(acceptance_lines)
        acceptance = package.setdefault("acceptance", {})
        if commands and not acceptance.get("commands"):
            acceptance["commands"] = commands
            filled.append("acceptance.commands")
        if thresholds and not acceptance.get("thresholds"):
            acceptance["thresholds"] = thresholds
            filled.append("acceptance.thresholds")

    merge_lines = _section(blocks, "merge order", "merge-order", "merge_order", "landing order")
    if merge_lines is not None:
        order = _parse_merge(merge_lines, [str(a.get("role")) for a in package.get("assignments") or []])
        if order and list(package.get("merge_order") or []) != order:
            package["merge_order"] = order
            filled.append("merge_order")

    _fill_from_turns(package, text, filled)
    return filled


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

    discussed = ""
    filled: list[str] = []
    if discuss and len(clean) >= 2:
        from .huddle import huddle

        record = huddle(cfg, board, clean, topic=brief.strip(), rounds=max(1, min(2, rounds)))
        discussed = record.get("path", "")
        package["minutes"] = discussed
        package["huddle"] = record.get("id", "")
        minutes_file = Path(discussed) if discussed else cfg.huddles_dir / f"{record.get('id', '')}.md"
        text = minutes_file.read_text(encoding="utf-8") if minutes_file.exists() else ""
        filled = fill_from_minutes(package, text)
        package["draft"] = bool(missing(package))
        save_package(cfg, group.id, package)
        board.log("kickoff_filled", group=group.id, package=package["id"], fields=filled,
                  draft=package["draft"])

    problems = missing(package)
    board.log("kickoff", group=group.id, package=package["id"], roles=clean, problems=len(problems),
              discussed=bool(discussed))
    return {
        "group": group.to_dict(),
        "package": package,
        "path": str(path),
        "minutes": discussed or str(minutes),
        "problems": problems,
    }


def import_package(cfg: TowerConfig, group_id: str, path: str | Path) -> dict[str, Any]:
    """Adopt a hand-authored package file, but only if it answers every agreement."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("a task package must be a JSON object")
    problems = validate(data)
    if problems:
        raise ValueError("incomplete task package:\n- " + "\n- ".join(problems))
    group = groups_mod.load(cfg, group_id)
    data["draft"] = False
    save_package(cfg, group_id, data)
    group.package = str(data.get("id") or group.package)
    if group.status == "created":
        group.status = "discussing"
    groups_mod.save(cfg, group)
    return data


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
