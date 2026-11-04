"""Prompt rendering and dispatch.

The tower's leverage comes from a strict contract in every prompt:

  * you own exactly these files
  * you write your deliverables + a REPORT file
  * you never claim a check you did not run

and from the fact that the lead never *accepts* a report without verifying it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .adapters import AdapterRequest, get_adapter
from .board import Board, Task, STATUS_CLAIMED, STATUS_IN_PROGRESS, STATUS_REPORTED, STATUS_FAILED
from .config import TowerConfig

CONTRACT = """## Contract (read this first)

You are the **{role}** role inside a control-tower workflow.

1. Workspace: `{workspace}`. Your own folder: `{role_dir}`.
2. Write scope: {scopes}
   Put deliverables there. Do not edit files outside your scope; if you need a
   file changed outside it, say so in the report instead of doing it.
3. When you are done, write `{report_file}` with exactly these sections:

   ```
   ## Status
   ready

   ## Summary
   <what you did, 2-5 sentences>

   ## Files
   - <path> — <what changed>

   ## Checks
   - <command you actually ran> → <result you actually saw>

   ## Open issues
   - <anything unfinished, or "none">

   ## Attention
   <only when Status is blocked/failed: exactly one concrete question a person can answer>
   ```

4. Never claim a check you did not run. The lead verifies independently.
5. Do not ask the human anything unless you are blocked. If something is
   ambiguous, pick the most defensible option and record the assumption in
   "Open issues". If a person genuinely must decide, use "## Status: blocked"
   and put exactly one concrete question under "## Attention".
6. Start the report with a status line, in this closed vocabulary:
   `progress` (still working) · `waiting` (turn finished, waiting for a trigger)
   · `ready` (deliverable ready for review) · `blocked` (need a human decision)
   · `failed` (gave up) · `dropped` (nothing to do).
   A prose status is rejected by the verifier.

## Task {task_id} ({batch})

{brief}
"""


def render_prompt(cfg: TowerConfig, task: Task) -> str:
    role = cfg.roles[task.role]
    role_dir = cfg.role_dir(task.role)
    scopes = ", ".join(f"`{s}`" for s in task.write_scopes) or "(the whole workspace)"
    return CONTRACT.format(
        role=task.role,
        workspace=cfg.workspace,
        role_dir=role_dir,
        scopes=scopes,
        report_file=role_dir / f"{task.id}-REPORT.md",
        task_id=task.id,
        batch=task.batch or "ad-hoc",
        brief=task.brief,
    )


def dispatch(
    cfg: TowerConfig,
    board: Board,
    task: Task,
    dry_run: bool = False,
    extra_options: dict[str, Any] | None = None,
) -> Task:
    """Run one task through its role's adapter."""
    role = cfg.roles[task.role]
    prompt = render_prompt(cfg, task)

    task_dir = cfg.task_dir(task.id)
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "prompt.md").write_text(prompt, encoding="utf-8")
    (task_dir / "brief.md").write_text(task.brief, encoding="utf-8")

    if dry_run:
        board.log("dispatch_dry_run", task=task.id, role=task.role, prompt_len=len(prompt))
        return task

    board.transition(task, STATUS_CLAIMED, note=f"dispatched to {role.name}")

    options = dict(cfg.adapter_options.get(role.adapter) or {})
    options.update(extra_options or {})
    adapter = get_adapter(role.adapter, options)

    board.transition(task, STATUS_IN_PROGRESS, note=f"adapter={adapter.name}")
    req = AdapterRequest(
        role=task.role,
        prompt=prompt,
        cwd=cfg.role_dir(task.role),
        timeout_s=role.timeout_s,
        env=role.env,
        task_id=task.id,
        options=options,
    )
    result = adapter.run(req)

    (task_dir / f"adapter-{adapter.name}.txt").write_text(result.text, encoding="utf-8")
    (task_dir / "adapter-meta.json").write_text(
        json.dumps(result.meta or {}, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if result.exit_code != 0:
        board.transition(task, STATUS_FAILED, note=f"adapter exit {result.exit_code}", output=result.text[:2000])
        return task

    report = parse_report(result.text)
    task.report = report
    task.state = report.get("state", "")
    task.attention = report.get("attention")
    board.add_assignment(task, adapter.name, result.exit_code)
    board.transition(task, STATUS_REPORTED, note="report filed", report=report)
    board.push_report(task, report)
    return task


def parse_report(text: str) -> dict[str, Any]:
    """Parse the role's markdown report into a dict.

    Missing sections are kept as empty lists — the verifier decides whether that
    is acceptable (see config `accept_requires`).
    """
    section: str | None = None
    buckets: dict[str, list[str]] = {}
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped.startswith("## "):
            section = stripped[3:].strip().lower()
            buckets.setdefault(section, [])
            continue
        if section:
            buckets[section].append(line)

    def items(name: str) -> list[str]:
        out = []
        for line in buckets.get(name, []):
            s = line.strip()
            if not s:
                continue
            if s.startswith(("-", "*", "•")):
                s = s[1:].strip()
            out.append(s)
        return out

    summary_lines = [l.strip() for l in buckets.get("summary", []) if l.strip()]
    status_lines = [l.strip() for l in buckets.get("status", []) if l.strip()]
    state = (status_lines[0] if status_lines else "").strip().strip("`").lower()
    attention_lines = []
    for line in buckets.get("attention", []):
        s2 = line.strip().lstrip("-*•").strip()
        if not s2 or s2.lower() in ("none", "n/a", "no", "-"):
            continue
        attention_lines.append(s2)
    question = attention_lines[0] if attention_lines else ""
    return {
        "state": state,
        "summary": " ".join(summary_lines),
        "files": items("files"),
        "checks": items("checks"),
        "open_issues": items("open issues"),
        "attention": {"question": question} if question else None,
        "raw": text,
    }
