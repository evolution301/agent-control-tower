"""Verification: the lead must never accept a claim it has not checked.

Two layers:

* structural — the report has the required sections and non-empty checks
* command  — optional `verify.commands` from tower.toml are executed and must
  pass before a task can be accepted
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import re

from .board import REPORT_STATES, Task
from .config import TowerConfig


@dataclass
class Verification:
    ok: bool
    problems: list[str] = field(default_factory=list)
    ran: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "problems": self.problems, "ran": self.ran}


def _expand_braces(text: str, limit: int = 64) -> list[str]:
    """Expand `{a,b}` groups the way a shell would, for file-existence checks.

    Only the first group is expanded per pass; unexpanded braces (or nonsense
    nesting) fall back to the literal string, so this can never make a real
    missing file look present.
    """
    out = [text]
    for _ in range(4):
        grown: list[str] = []
        changed = False
        for item in out:
            open_at = item.find("{")
            close_at = item.find("}", open_at + 1) if open_at >= 0 else -1
            if open_at < 0 or close_at < 0:
                grown.append(item)
                continue
            changed = True
            head, body, tail = item[:open_at], item[open_at + 1:close_at], item[close_at + 1:]
            grown.extend(head + option + tail for option in body.split(","))
            if len(grown) > limit:
                return [text]
        out = grown
        if not changed:
            break
    return out


_FILE_SPLIT = re.compile(r"[、,;，；|·•\t]+")


def split_file_candidates(line: str) -> list[str]:
    """B14: one report line may list several paths joined by 、 , ; | — split them.

    Each segment is checked on its own; a path with an annotation (` — why`) keeps
    the old behaviour of checking the path part only. A line carrying a brace
    pattern (`shots/{home,faq}.png`) is NOT split: its commas belong to the
    pattern, which `_expand_braces` handles.
    """
    base = line.split(" — ")[0].split(" - ")[0].strip().strip("`")
    if "{" in base and "}" in base and base.find("{") < base.find("}"):
        return [base]
    parts = [seg.strip().strip("`") for seg in _FILE_SPLIT.split(base)]
    return [part for part in parts if part]


def _fresh_report(cfg: TowerConfig, task: Task) -> dict[str, Any] | None:
    """B15: prefer the markdown report on disk over the cached copy in the task.

    A role (or the lead) may edit `<task>-REPORT.md` after filing; accepting must
    see those edits, not the stale parse stored in `.tower/tasks/T-….json`.
    """
    from .dispatch import parse_report

    candidates = []
    try:
        candidates.append(Path(cfg.role_dir(task.role)) / f"{task.id}-REPORT.md")
    except (AttributeError, TypeError):
        pass
    candidates.append(Path(cfg.workspace) / f"{task.id}-REPORT.md")
    project_root = getattr(cfg, "project_root", None)
    if project_root:
        candidates.append(Path(project_root) / f"{task.id}-REPORT.md")
    best: Path | None = None
    best_mtime = -1.0
    for candidate in candidates:
        try:
            mtime = candidate.stat().st_mtime
        except OSError:
            continue
        if mtime > best_mtime:
            best, best_mtime = candidate, mtime
    if best is None:
        return None
    try:
        text = best.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    parsed = parse_report(text)
    if not (parsed.get("state") or parsed.get("summary")
            or parsed.get("files") or parsed.get("checks")):
        return None
    return parsed


def _before(task: Task, since: str) -> bool:
    """Is the task older than `since` (ISO ts)? Unparseable input answers False."""
    from datetime import datetime

    try:
        boundary = datetime.strptime(since.strip(), "%Y-%m-%dT%H:%M:%S%z")
        created = datetime.strptime((task.created_at or "").strip(), "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return False
    return created < boundary


def verify_report(cfg: TowerConfig, task: Task, legacy: bool = False) -> Verification:
    problems: list[str] = []
    # B15: the markdown file wins over the cached parse when it exists
    report = _fresh_report(cfg, task) or task.report or {}

    requirements = {
        "summary": lambda r: bool(r.get("summary")),
        "files": lambda r: bool(r.get("files")),
        "checks": lambda r: bool(r.get("checks")),
    }

    # v0.2 — structured state, not prose. Borrowed from dsh-superagent: the lead
    # should be able to route on a closed vocabulary instead of reading paragraphs.
    state = (report.get("state") or "").strip().lower()
    if not state:
        problems.append("report is missing '## Status' (progress|waiting|ready|blocked|failed|dropped)")
    elif state not in REPORT_STATES:
        problems.append(f"unknown status '{state}' — expected one of {sorted(REPORT_STATES)}")
    elif state in ("blocked", "failed") and not (report.get("attention") or {}).get("question"):
        problems.append(f"status '{state}' requires one concrete question under '## Attention'")
    requirements["self_review"] = lambda r: bool((r.get("self_review") or "").strip())
    self_review_required = bool(getattr(cfg, "self_review", True)) and not legacy
    since = str(getattr(cfg, "self_review_since", "") or "").strip()
    if self_review_required and since and _before(task, since):
        self_review_required = False

    for need in cfg.accept_requires:
        if need == "self_review" and not self_review_required:
            continue
        check = requirements.get(need)
        if check is None:
            continue
        if not check(report):
            problems.append(f"report is missing '{need}'")

    # v0.10 — the self-review gate (pstack: 写完 ≠ 完成). When the tower turns it
    # on, a report without a substantive `## Self-review` line cannot pass, no
    # matter what accept_requires says.
    if self_review_required and not requirements["self_review"](report):
        problems.append(
            "report is missing '## Self-review' — re-read your own changes and fix "
            "issues BEFORE filing; one line on what you reviewed and fixed, or "
            "'no findings — checks re-run'")

    # A report that lists a check but never says what happened is not a check.
    weak = [c for c in report.get("checks", []) if "→" not in c and "->" not in c]
    if report.get("checks") and len(weak) == len(report.get("checks", [])):
        problems.append("every '## Checks' line lacks a '→ result' — ask for real output")

    # Files must exist inside the workspace or the project root. Roles routinely
    # write project deliverables into a sibling directory (the tower workspace is
    # the coordination root, not always the code root), so checking only the
    # workspace both rejects real files and lets a wrong-root write pass.
    roots = [cfg.workspace]
    project_root = getattr(cfg, "project_root", None)
    if project_root and Path(project_root) != Path(cfg.workspace):
        roots.append(Path(project_root))
    # If the checks run in the project, a report about that tree is about that
    # directory — resolve those paths there too.
    raw_verify_cwd = str(getattr(cfg, "verify_cwd", "") or "").strip()
    if raw_verify_cwd:
        extra = Path(raw_verify_cwd).expanduser()
        if not extra.is_absolute():
            extra = (Path(cfg.workspace) / extra).resolve()
        if extra not in roots:
            roots.append(extra)
    missing = []
    for f in report.get("files", []):
        candidates = split_file_candidates(str(f))
        if not candidates:
            continue
        for candidate in candidates:
            # A report often summarises a set with a shell-style brace pattern
            # (`shots/{home,faq}-{1440,390}.png`). Every expansion must exist: a
            # pattern that names files the role did not produce is still a false
            # claim, and an unexpandable string falls back to the literal path.
            expanded = _expand_braces(candidate)
            found = True
            for item in expanded:
                path = Path(item)
                if path.is_absolute():
                    if not path.exists():
                        found = False
                        break
                elif not any((root / item).exists() for root in roots):
                    found = False
                    break
            if not found:
                missing.append(candidate)
    if missing:
        problems.append("reported files do not exist (checked: "
                        + ", ".join(str(r) for r in roots) + "): " + ", ".join(missing[:5]))

    ran: list[dict[str, Any]] = []
    commands = list(getattr(cfg, "verify_commands", []) or [])
    # cwd for verify commands: `verify_cwd` when set (relative paths resolve
    # against the workspace), else the workspace. A relative script path plus the
    # workspace cwd is the classic "every accept fails with MODULE_NOT_FOUND".
    raw_cwd = str(getattr(cfg, "verify_cwd", "") or "").strip()
    if raw_cwd:
        verify_dir = Path(raw_cwd).expanduser()
        if not verify_dir.is_absolute():
            verify_dir = (Path(cfg.workspace) / verify_dir).resolve()
    else:
        verify_dir = Path(cfg.workspace)
    for cmd in commands:
        try:
            proc = subprocess.run(
                cmd, shell=True, cwd=str(verify_dir), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=900,
            )
            tail = (proc.stdout or proc.stderr or "")[-800:]
            ran.append({"command": cmd, "cwd": str(verify_dir), "exit_code": proc.returncode, "tail": tail})
            if proc.returncode != 0:
                lowered = tail.lower()
                if proc.returncode == 127 or "not found" in lowered or "not recognized" in lowered:
                    problems.append(
                        f"verify command is not runnable from {verify_dir} ({proc.returncode}): {cmd} "
                        "— likely a relative path; use an absolute one"
                    )
                else:
                    problems.append(f"verify command failed ({proc.returncode}): {cmd}")
        except Exception as exc:  # pragma: no cover
            ran.append({"command": cmd, "error": f"{type(exc).__name__}: {exc}"})
            problems.append(f"verify command error: {cmd}")

    return Verification(ok=not problems, problems=problems, ran=ran)
