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
from .board import (
    Board,
    Task,
    STATUS_CLAIMED,
    STATUS_IN_PROGRESS,
    STATUS_REPORTED,
    STATUS_FAILED,
    STATUS_RESTATING,
    now_ts,
)
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

{confirmed}## Task {task_id} ({batch})

{brief}
"""


# v0.8 — the restate gate (playbook point 7: "voice brain-dump, then restate it").
# The first answer is deliberately *not* work: it is the role's own words about
# the brief, so a misread costs one short turn instead of a full dispatch.
RESTATE_CONTRACT = """## Restatement gate (read this first)

You are the **{role}** role inside a control-tower workflow. This turn is a
**restatement only**. Do not start the work, do not write deliverables, do not
run commands.

1. Workspace: `{workspace}`. Your own folder: `{role_dir}`.
2. Read the brief below and answer in your own words, in exactly these sections:

   ```
   ## Restatement
   <what you are being asked to do, 3-6 sentences, in your own words — not a copy>

   ## Assumptions
   - <the readings of the brief you had to pick between, one per line>
   - <or "none">

   ## Done looks like
   <the single observable fact that would make you call this finished>
   ```

3. If the brief is too ambiguous to restate, say so plainly in
   `## Restatement` and put the one question you need under `## Assumptions`.
4. Nothing else: no files, no checks, no status line. A person accepts the
   restatement with `tower restate ok {task_id}` before any work is dispatched.

## Task {task_id} ({batch})

{brief}
"""


def _confirmed_block(task: Task) -> str:
    """The restatement a person already accepted, carried into the work prompt."""
    if not (task.restate_ok and task.restatement.strip()):
        return ""
    return (
        "## Confirmed understanding (a person accepted this before you started)\n\n"
        f"{task.restatement.strip()}\n\n"
        "Work to that understanding. If it contradicts the brief below, do not\n"
        "guess: report `## Status: blocked` with the one question that resolves it.\n\n"
    )


def render_prompt(cfg: TowerConfig, task: Task) -> str:
    role = cfg.roles[task.role]
    role_dir = cfg.role_dir(task.role)
    scopes = ", ".join(f"`{s}`" for s in task.write_scopes) or "(the whole workspace)"
    prompt = CONTRACT.format(
        role=task.role,
        workspace=cfg.workspace,
        role_dir=role_dir,
        scopes=scopes,
        report_file=role_dir / f"{task.id}-REPORT.md",
        task_id=task.id,
        batch=task.batch or "ad-hoc",
        confirmed=_confirmed_block(task),
        brief=task.brief,
    )
    # Playbook point 3: the role inherits what its past accepted jobs taught —
    # as a one-line-per-skill index, never the bodies (context window guard).
    from .skills import skill_index_lines

    index = skill_index_lines(cfg, task.role)
    if index:
        prompt += (
            "\n## Skills from jobs you already did well\n\n"
            + "\n".join(index)
            + "\nEach line names a skill under `"
            + str(role_dir / "skills")
            + "/` — open one for the full steps. Follow them as the default way of "
            "working; deviate only with the reason recorded in the report.\n"
        )
    return prompt


def render_restate_prompt(cfg: TowerConfig, task: Task) -> str:
    """The restatement prompt: same task metadata, no deliverables asked for."""
    role_dir = cfg.role_dir(task.role)
    return RESTATE_CONTRACT.format(
        role=task.role,
        workspace=cfg.workspace,
        role_dir=role_dir,
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


def dispatch_restate(
    cfg: TowerConfig,
    board: Board,
    task: Task,
    dry_run: bool = False,
) -> Task:
    """Ask a role to restate the brief; dispatch no work (playbook point 7).

    The gate fails closed. A non-zero adapter exit, a missing `## Restatement`
    section, or an empty restatement leaves the task out of `restating`, so a
    silent or confused adapter can never be mistaken for agreement. A task that
    does reach `restating` has no report and cannot be accepted: only
    `tower restate ok <task>` unlocks the actual dispatch.
    """
    role = cfg.roles[task.role]
    prompt = render_restate_prompt(cfg, task)

    task_dir = cfg.task_dir(task.id)
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "restate-prompt.md").write_text(prompt, encoding="utf-8")

    if dry_run:
        board.log("restate_dry_run", task=task.id, role=task.role, prompt_len=len(prompt))
        return task

    board.transition(task, STATUS_CLAIMED, note=f"restatement requested from {role.name}")

    options = dict(cfg.adapter_options.get(role.adapter) or {})
    adapter = get_adapter(role.adapter, options)

    board.transition(task, STATUS_IN_PROGRESS, note=f"adapter={adapter.name} (restate only)")
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

    (task_dir / f"restate-{adapter.name}.txt").write_text(result.text, encoding="utf-8")
    board.add_assignment(task, adapter.name, result.exit_code)

    if result.exit_code != 0:
        board.transition(task, STATUS_FAILED, note=f"restate adapter exit {result.exit_code}", output=result.text[:2000])
        # a failed GATE is a person decision (rewrite the brief), not a retry:
        # auto-triage would re-send the same words and fail the same way.
        task.state = "blocked"
        task.attention = {
            "question": f"Restatement gate failed for {task.id} (adapter exit "
                        f"{result.exit_code}) — rewrite the brief, then re-dispatch.",
        }
        board.save(task)
        return task

    parsed = parse_restatement(result.text)
    if not parsed["restatement"]:
        board.transition(task, STATUS_FAILED, note="adapter returned no '## Restatement' section")
        # same as above: a gate that fails closed is a person decision
        task.state = "blocked"
        task.attention = {
            "question": f"Restatement gate failed for {task.id} — the adapter never "
                        "answered with a '## Restatement' section. Rewrite the brief, "
                        "then re-dispatch.",
        }
        board.save(task)
        return task

    task.restatement = parsed["restatement"]
    task.restated_at = now_ts()
    (task_dir / "restatement.md").write_text(result.text, encoding="utf-8")
    task.attention = {
        "question": f"Restatement for {task.id} — is this the brief you meant?",
        "options": [f"tower restate ok {task.id}"],
    }
    board.transition(task, STATUS_RESTATING, note="restatement filed; no work dispatched yet")
    board.log(
        "restatement",
        task=task.id,
        role=task.role,
        chars=len(task.restatement),
        assumptions=len(parsed["assumptions"]),
    )
    return task


def _sections(text: str) -> dict[str, list[str]]:
    """Split a markdown answer into `## Heading` -> lines (heading lower-cased)."""
    buckets: dict[str, list[str]] = {}
    section: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped.startswith("## "):
            section = stripped[3:].strip().lower()
            buckets.setdefault(section, [])
            continue
        if section:
            buckets[section].append(line)
    return buckets


def _strip_bullet(line: str) -> str:
    s = line.strip()
    if s.startswith(("-", "*", "•")):
        s = s[1:].strip()
    return s


def parse_report(text: str) -> dict[str, Any]:
    """Parse the role's markdown report into a dict.

    Missing sections are kept as empty lists — the verifier decides whether that
    is acceptable (see config `accept_requires`).
    """
    buckets = _sections(text)

    def items(name: str) -> list[str]:
        return [_strip_bullet(line) for line in buckets.get(name, []) if line.strip()]

    summary_lines = [l.strip() for l in buckets.get("summary", []) if l.strip()]
    status_lines = [l.strip() for l in buckets.get("status", []) if l.strip()]
    state = (status_lines[0] if status_lines else "").strip().strip("`").lower()
    attention_lines = []
    for line in buckets.get("attention", []):
        s2 = _strip_bullet(line)
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


def parse_restatement(text: str) -> dict[str, Any]:
    """Parse the answer to the restate gate (see `RESTATE_CONTRACT`).

    An empty `restatement` means the role did not answer the question that was
    asked; the caller must treat that as a failure, not as agreement.
    """
    buckets = _sections(text)
    restatement = "\n".join(line.rstrip() for line in buckets.get("restatement", [])).strip()
    assumptions = []
    for line in buckets.get("assumptions", []):
        item = _strip_bullet(line)
        if item and item.lower() not in ("none", "n/a", "-"):
            assumptions.append(item)
    done = " ".join(line.strip() for line in buckets.get("done looks like", []) if line.strip())
    return {"restatement": restatement, "assumptions": assumptions, "done": done, "raw": text}
