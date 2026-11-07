"""Onboard: interview the operator, then grow the team from the answers.

Playbook points 1 and 6. "让 AI 设计 AI" — but the tower is deliberately
model-free, so the *interview* is structured instead of hallucinated: the five
questions from the playbook (what you do, which step costs the most, who reviews,
where the material lives, what done looks like), answered either interactively or
in a notes file (the voice-brain-dump case: `--from notes.md`).

The notes format is markdown and boring on purpose:

    # What I do
    I run a small B2B site studio.

    ## backend
    builds and maintains the server side
    scope: src/server/
    scope: tests/

    ## frontend
    builds the browser UI
    scope: src/web/

Every `## <name>` section becomes one role: the free text is the description,
each `scope:` line becomes a write scope. Everything else in the file is kept
verbatim in `PLAN.md`, so the five answers stay on record next to the team they
produced. Idempotent: re-running with `--force` replaces the roles it created
before, and only them.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from .roles import add_role

QUESTIONS = [
    "What do you do? (the work this team exists for)",
    "Which step costs you the most time right now?",
    "Who reviews finished work, and how?",
    "Where does the material live? (folders, repos, tools)",
    "What does 'done' look like? (the observable fact)",
]

SECTION_HEADINGS = ("what i do", "which step costs", "who reviews",
                    "where the material", "what does", "done looks like")


def parse_notes(text: str) -> tuple[list[dict], str]:
    """`## <name>` sections become roles; everything else is the operator's plan."""
    roles: list[dict] = []
    plan_lines: list[str] = []
    current_role: dict | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if stripped.startswith("## "):
            name = stripped[3:].strip()
            if name and " " not in name:
                current_role = {"name": name, "description": "", "write_scopes": []}
                roles.append(current_role)
                continue
            current_role = None
        elif stripped.startswith("# "):
            current_role = None
        if current_role is not None:
            if stripped.lower().startswith("scope:"):
                scope = stripped.split(":", 1)[1].strip()
                if scope:
                    current_role["write_scopes"].append(scope)
            elif stripped:
                current_role["description"] = (current_role["description"] + " " + stripped).strip()
        else:
            plan_lines.append(line)
    return roles, "\n".join(plan_lines).strip()


def interactive_answers() -> str:
    """Ask the five questions; requires a human at a terminal."""
    if not sys.stdin.isatty():
        raise ValueError(
            "onboarding needs answers: pass --from notes.md, or run this in a terminal"
        )
    out = ["# What I do"]
    for i, question in enumerate(QUESTIONS):
        print(f"\n{i + 1}/5  {question}")
        answer = input("> ").strip()
        heading = ["What I do", "Which step costs the most", "Who reviews",
                   "Where the material lives", "What done looks like"][i]
        if i and heading not in out:
            out.append(f"\n# {heading}")
        out.append(answer)
    out.append("\nNow add one section per role you need (see the onboard docstring "
               "for the format), then re-run with --from this file.")
    return "\n".join(out)


def onboard(
    config_path: Path,
    notes: str = "",
    notes_path: Path | None = None,
    adapter: str = "dsh",
    max_roles: int = 6,
    force: bool = False,
) -> dict:
    """Grow the team from notes; write PLAN.md with the operator's answers."""
    if notes_path is None and not notes:
        notes = interactive_answers()
    if notes_path is not None:
        notes = Path(notes_path).read_text(encoding="utf-8")
    roles, plan = parse_notes(notes)
    if not roles:
        raise ValueError(
            "no '## <role>' sections found in the notes — add one section per role "
            "(free text = description, 'scope:' lines = write scopes)"
        )
    if len(roles) > max_roles:
        raise ValueError(
            f"{len(roles)} roles is more than the playbook's 'few, well-trained' "
            f"(cap {max_roles}); start small, split later"
        )
    if config_path.suffix == ".json":
        raise ValueError(
            "roles are line-edited into tower.toml; onboard a workspace that has one "
            "(tower init .), not a JSON config"
        )
    created = []
    for spec in roles:
        info = add_role(
            config_path,
            spec["name"],
            description=spec["description"],
            adapter=adapter,
            write_scopes=spec["write_scopes"],
            force=force,
        )
        created.append(info)
    plan_path = config_path.parent / "PLAN.md"
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    plan_path.write_text(
        f"# Onboarding plan ({stamp})\n\n"
        "Team grown from the operator's answers; the raw answers are kept below.\n\n"
        "## Roles\n\n"
        + "\n".join(
            f"- **{r['name']}** — {s['description']} (scope: "
            + (", ".join(r['write_scopes']) or "whole workspace") + ")"
            for r, s in zip(created, roles)
        )
        + "\n\n## Answers\n\n"
        + (plan or "(none)")
        + "\n",
        encoding="utf-8",
    )
    return {"roles": [r["name"] for r in created], "plan": str(plan_path), "created": created}
