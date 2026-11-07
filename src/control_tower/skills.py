"""Skills: what the tower learned from jobs it already accepted (playbook point 3).

"AI 做对一次，就保存步骤、格式和检查规则，下次直接复用。" The unit of *did well*
here is deliberately strict: only an **accepted** task can become a skill, and
acceptance already means the report was verified — real files, real checks. A
skill is the accepted task distilled into three sections (steps, output format,
checks) plus a one-line index entry.

Dispatch injects **only the index**, one line per skill, never the full text:
feeding every skill wholesale would bloat the context window (the pstack rule we
borrowed and the reason the index stays one line). The full skill file lives in
`roles/<role>/skills/` where the role — or a person — can open it on purpose.
"""

from __future__ import annotations

import re
from pathlib import Path

from .board import Board, STATUS_ACCEPTED
from .config import TowerConfig

SLUG_RE = re.compile(r"[^a-z0-9]+")


def skills_dir(cfg: TowerConfig, role: str) -> Path:
    return cfg.role_dir(role) / "skills"


def skill_path(cfg: TowerConfig, role: str, slug: str) -> Path:
    return skills_dir(cfg, role) / f"{slug}.md"


def slug_for(task_id: str, brief: str) -> str:
    base = SLUG_RE.sub("-", (brief or "").lower()).strip("-")[:40].strip("-")
    return base or f"task-{task_id.lower()}"


def render_skill(role: str, task_id: str, brief: str, report: dict) -> str:
    checks = report.get("checks") or []
    files = report.get("files") or []
    steps = [f"{i}. {line}" for i, line in enumerate(
        [report.get("summary", "")] + [c.split("→")[0].strip() for c in checks], start=1)]
    return (
        f"# Skill: {brief.strip()[:80]}\n\n"
        f"> {report.get('summary', '').strip()[:100]}\n\n"
        f"Distilled from task {task_id} (role: {role}), which passed verification.\n\n"
        "## Steps\n\n" + "\n".join(steps) + "\n\n"
        "## Output format\n\n"
        + ("\n".join(f"- {f}" for f in files) or "- (as the brief asked)")
        + "\n\n## Checks\n\n"
        + ("\n".join(f"- {c}" for c in checks) or "- none recorded")
        + "\n"
    )


def rebuild_index(cfg: TowerConfig, role: str) -> list[str]:
    """One line per skill file: `- <slug> — <summary>`. Returns the lines."""
    directory = skills_dir(cfg, role)
    lines: list[str] = []
    if directory.exists():
        for path in sorted(directory.glob("*.md")):
            if path.name == "INDEX.md":
                continue
            summary = ""
            for raw in path.read_text(encoding="utf-8").splitlines():
                text = raw.strip()
                if text.startswith("> "):
                    summary = text[2:].strip()[:100]
                    break
            lines.append(f"- {path.stem} — {summary}")
    index = directory / "INDEX.md"
    if lines:
        index.write_text(
            "# Skill index\n\nOne line per skill; open the file for the full steps.\n\n"
            + "\n".join(lines) + "\n",
            encoding="utf-8",
        )
    elif index.exists():
        index.unlink()
    return lines


def save_skill(cfg: TowerConfig, board: Board, task_id: str) -> dict:
    """Turn an accepted task into a skill file + index entry. Fails loudly otherwise."""
    task = board.load(task_id)
    if task.status != STATUS_ACCEPTED:
        raise ValueError(
            f"{task_id} is {task.status}, not accepted — only verified work becomes a skill"
        )
    report = task.report or {}
    if not report.get("checks"):
        raise ValueError(f"{task_id} has no checks; nothing verifiable to distil")
    slug = slug_for(task.id, task.brief)
    path = skill_path(cfg, task.role, slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_skill(task.role, task.id, task.brief, report), encoding="utf-8")
    lines = rebuild_index(cfg, task.role)
    board.log("skill_saved", task=task.id, role=task.role, slug=slug, skills=len(lines))
    return {"role": task.role, "slug": slug, "path": str(path), "index_lines": lines}


def skill_index_lines(cfg: TowerConfig, role: str) -> list[str]:
    """The injection payload: the index only, never the skill bodies."""
    directory = skills_dir(cfg, role)
    index = directory / "INDEX.md"
    if not index.exists():
        return []
    out = []
    for raw in index.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("- "):
            out.append(line)
    return out
