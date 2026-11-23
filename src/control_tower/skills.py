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


# ---------------------------------------------------------------------------
# v3 §5 — the company-wide library.
#
# Until now a skill lived in `roles/<role>/skills/`, so the next bot with the
# same problem started from zero. A skill is company knowledge: any lane may
# read it, any lane may patch it, and the index — not the body — is what gets
# injected into a brief.
# ---------------------------------------------------------------------------

def library_root(cfg: TowerConfig) -> Path:
    path = cfg.skills_dir
    path.mkdir(parents=True, exist_ok=True)
    return path


META_RE = re.compile(r"<!--\s*skill:\s*(?P<body>.*?)-->", re.S)


def _meta(path: Path) -> dict[str, str]:
    try:
        head = path.read_text(encoding="utf-8")[:600]
    except OSError:
        return {}
    found = META_RE.search(head)
    if not found:
        return {}
    out: dict[str, str] = {}
    for part in found.group("body").split(";"):
        key, _, value = part.partition("=")
        if key.strip():
            out[key.strip()] = value.strip()
    return out


def _title_and_summary(path: Path) -> tuple[str, str, str]:
    title, summary = path.stem, ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return title, summary, ""
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("# ") and title == path.stem:
            title = line[2:].strip()[:80]
        elif line.startswith("> ") and not summary:
            summary = line[2:].strip()[:120]
    return title, summary, text


def library_entries(cfg: TowerConfig) -> list[dict]:
    """Every skill in the tower: role skills plus the shared library folder."""
    entries: list[dict] = []
    seen: set[str] = set()

    def add(path: Path, role: str, shared: bool) -> None:
        ident = f"{role}/{path.stem}"
        if ident in seen:
            return
        seen.add(ident)
        meta = _meta(path)
        title, summary, body = _title_and_summary(path)
        entries.append({
            "id": ident,
            "role": role,
            "slug": path.stem,
            "shared": shared,
            "title": title,
            "summary": summary or meta.get("summary", ""),
            "version": int(meta.get("version", "1") or 1),
            "updated": meta.get("updated", ""),
            "path": str(path),
            "body": body,
        })

    for name in cfg.roles:
        directory = skills_dir(cfg, name)
        if directory.exists():
            for path in sorted(directory.glob("*.md")):
                if path.name == "INDEX.md":
                    continue
                add(path, name, False)
    root = library_root(cfg)
    for path in sorted(root.glob("**/*.md")):
        if path.name in ("INDEX.md", "README.md") or ".history" in path.parts:
            continue
        add(path, "shared", True)
    return entries


def search(cfg: TowerConfig, query: str, limit: int = 8) -> list[dict]:
    """Keyword search over title, summary and headings — no embeddings, on purpose."""
    words = [w for w in re.split(r"[^0-9a-zA-Z\u4e00-\u9fff]+", (query or "").lower()) if len(w) > 1]
    scored: list[tuple[int, dict]] = []
    for entry in library_entries(cfg):
        hay = " ".join([entry["id"], entry["title"], entry["summary"], entry["body"][:4000]]).lower()
        score = sum(hay.count(word) for word in words)
        if score or not words:
            scored.append((score, entry))
    scored.sort(key=lambda pair: (-pair[0], pair[1]["id"]))
    out = []
    for _, entry in scored[: max(1, limit)]:
        out.append({k: entry[k] for k in ("id", "role", "title", "summary", "version", "path")})
    return out


def resolve(cfg: TowerConfig, name: str) -> dict:
    """Find a skill by id (`role/slug`), slug, or title prefix."""
    key = (name or "").strip().lower()
    entries = library_entries(cfg)
    for entry in entries:
        if entry["id"].lower() == key:
            return entry
    hits = [e for e in entries if key and (key in e["slug"].lower() or key in e["title"].lower())]
    if len(hits) == 1:
        return hits[0]
    if hits:
        raise KeyError(f"ambiguous skill {name!r}: " + ", ".join(e["id"] for e in hits[:6]))
    raise KeyError(f"no skill matched {name!r} — `tower skill search <words>`")


def patch(cfg: TowerConfig, name: str, body: str, note: str = "", by: str = "lead") -> dict:
    """Version a skill up; the previous body is kept under .history/."""
    import datetime

    entry = resolve(cfg, name)
    path = Path(entry["path"])
    history = library_root(cfg) / ".history" / entry["role"] / entry["slug"]
    history.mkdir(parents=True, exist_ok=True)
    previous = path.read_text(encoding="utf-8") if path.exists() else ""
    if previous:
        (history / f"v{entry['version']}.md").write_text(previous, encoding="utf-8")
    version = entry["version"] + 1
    stamp = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    header = (
        f"<!-- skill: id={entry['id']}; version={version}; updated={stamp}; "
        f"by={by}; note={note[:120]} -->\n\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + body.strip() + "\n", encoding="utf-8")
    rebuild_library_index(cfg)
    return {"id": entry["id"], "version": version, "path": str(path), "previous": entry["version"]}


def rebuild_library_index(cfg: TowerConfig) -> Path:
    entries = library_entries(cfg)
    lines = ["# 公司技能库", "", f"共 {len(entries)} 条；按角色检索：`tower skill search <关键词>`", ""]
    for role in sorted({entry["role"] for entry in entries}):
        lines.append(f"## {role}")
        lines.append("")
        for entry in sorted([e for e in entries if e["role"] == role], key=lambda e: e["id"]):
            lines.append(f"- {entry['id']}（v{entry['version']}）— {entry['summary'] or entry['title']}")
        lines.append("")
    path = library_root(cfg) / "INDEX.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def library_index_lines(cfg: TowerConfig, query: str = "", limit: int = 4) -> list[str]:
    """The cross-role slice injected into a brief: matches only, one line each."""
    if not (query or "").strip():
        return []
    hits = search(cfg, query, limit=limit)
    out = []
    for hit in hits:
        out.append(f"- {hit['id']} — {hit['summary'] or hit['title']} (open: tower skill show {hit['id']})")
    return out


def stats(cfg: TowerConfig) -> list[dict]:
    entries = library_entries(cfg)
    rows: dict[str, dict] = {}
    for entry in entries:
        row = rows.setdefault(entry["role"], {"role": entry["role"], "skills": 0, "max_version": 1, "latest": ""})
        row["skills"] += 1
        row["max_version"] = max(row["max_version"], entry["version"])
        row["latest"] = max(row["latest"], entry["updated"])
    return sorted(rows.values(), key=lambda r: r["role"])
