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

from .board import REPORT_STATES, Task
from .config import TowerConfig


@dataclass
class Verification:
    ok: bool
    problems: list[str] = field(default_factory=list)
    ran: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "problems": self.problems, "ran": self.ran}


def verify_report(cfg: TowerConfig, task: Task) -> Verification:
    problems: list[str] = []
    report = task.report or {}

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

    for need in cfg.accept_requires:
        check = requirements.get(need)
        if check is None:
            continue
        if not check(report):
            problems.append(f"report is missing '{need}'")

    # v0.10 — the self-review gate (pstack: 写完 ≠ 完成). When the tower turns it
    # on, a report without a substantive `## Self-review` line cannot pass, no
    # matter what accept_requires says.
    if getattr(cfg, "self_review", True) and not requirements["self_review"](report):
        problems.append(
            "report is missing '## Self-review' — re-read your own changes and fix "
            "issues BEFORE filing; one line on what you reviewed and fixed, or "
            "'no findings — checks re-run'")

    # A report that lists a check but never says what happened is not a check.
    weak = [c for c in report.get("checks", []) if "→" not in c and "->" not in c]
    if report.get("checks") and len(weak) == len(report.get("checks", [])):
        problems.append("every '## Checks' line lacks a '→ result' — ask for real output")

    # Files must exist inside the workspace.
    missing = []
    for f in report.get("files", []):
        candidate = f.split(" — ")[0].split(" - ")[0].strip().strip("`")
        if not candidate:
            continue
        path = Path(candidate)
        if not path.is_absolute():
            path = cfg.workspace / candidate
        if not path.exists():
            missing.append(candidate)
    if missing:
        problems.append("reported files do not exist: " + ", ".join(missing[:5]))

    ran: list[dict[str, Any]] = []
    commands = list(getattr(cfg, "verify_commands", []) or [])
    for cmd in commands:
        try:
            proc = subprocess.run(
                cmd, shell=True, cwd=str(cfg.workspace), capture_output=True, text=True, timeout=900
            )
            ran.append({"command": cmd, "exit_code": proc.returncode, "tail": (proc.stdout or proc.stderr or "")[-800:]})
            if proc.returncode != 0:
                problems.append(f"verify command failed ({proc.returncode}): {cmd}")
        except Exception as exc:  # pragma: no cover
            ran.append({"command": cmd, "error": f"{type(exc).__name__}: {exc}"})
            problems.append(f"verify command error: {cmd}")

    return Verification(ok=not problems, problems=problems, ran=ran)
