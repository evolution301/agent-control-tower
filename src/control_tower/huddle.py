"""Huddles: up to six roles in one room, then everybody goes back to work.

A huddle is bounded on purpose. Round 1 each role states a position and the
questions it needs answered; round 2 each role reads the others and commits to
what it will actually do. Bounded rounds are what keep a "discussion" from turning
into a meeting that never ends: after the last round every participant has a
commitment on the record, and the lead has one file with all of it.

The lead opens the room. The roles never wait on each other directly - same relay
rule as `tower say`: the tower carries the turns and files them.
"""

from __future__ import annotations

import json
from pathlib import Path

from .board import Board, now_ts
from .config import TowerConfig
from .converse import run_turn
from .dispatch import _sections

HUDDLE_MAX_ROLES = 6

ROUND1_CONTRACT = """## Huddle round 1 — {topic}

You are the **{role}** role in a huddle with: {others}. The lead opened this room
to settle the question above before anyone goes back to work.

This is a discussion turn. Answer in your own words; do not write deliverables.

    ## Position
    <where you stand on the question, 3-6 sentences, including what you would do>

    ## Questions
    - <what you need another role to answer before you can commit>
    - <or "none">

The lead reads every position and carries yours into round 2.
"""

ROUND2_CONTRACT = """## Huddle round 2 — {topic}

You are the **{role}** role. Round 1 positions from the other roles:

{positions}

Answer with exactly these sections:

    ## Decision
    <the option you now back, and why — change your mind if the others are right>

    ## Commitments
    - <what you will do first, once the room closes>
    - <what you need from someone else, or "none">
"""


def _section(text: str, key: str) -> str:
    return "\n".join(line.rstrip() for line in _sections(text).get(key, [])).strip()


def _bullets(text: str, key: str) -> list[str]:
    from .dispatch import _strip_bullet

    out = []
    for line in _sections(text).get(key, []):
        item = _strip_bullet(line)
        if item and item.lower() not in ("none", "n/a", "-"):
            out.append(item)
    return out


def parse_position(text: str) -> dict:
    return {"position": _section(text, "position"), "questions": _bullets(text, "questions")}


def parse_decision(text: str) -> dict:
    return {"decision": _section(text, "decision"), "commitments": _bullets(text, "commitments")}


def next_huddle_id(cfg: TowerConfig) -> str:
    cfg.huddles_dir.mkdir(parents=True, exist_ok=True)
    highest = 0
    for path in cfg.huddles_dir.glob("H-*.json"):
        try:
            highest = max(highest, int(path.stem.split("-")[1]))
        except (IndexError, ValueError):
            continue
    return f"H-{highest + 1:04d}"


def render_round1(role: str, others: list[str], topic: str) -> str:
    return ROUND1_CONTRACT.format(
        role=role,
        others=", ".join(others) if others else "(no one else)",
        topic=topic.strip(),
    )


def render_round2(role: str, topic: str, positions: dict[str, str]) -> str:
    rendered = "\n\n".join(
        f"**{name}**: {text.strip() or '(no position given)'}" for name, text in positions.items()
    )
    return ROUND2_CONTRACT.format(role=role, topic=topic.strip(), positions=rendered or "(empty)")


def huddle(
    cfg: TowerConfig,
    board: Board,
    roles: list[str],
    topic: str,
    rounds: int = 2,
    max_roles: int = HUDDLE_MAX_ROLES,
) -> dict:
    """Run one bounded huddle. Returns the record; raises on bad input."""
    unique: list[str] = []
    for role in roles:
        if role not in cfg.roles:
            raise KeyError(f"unknown role {role!r}; known: {', '.join(sorted(cfg.roles))}")
        if role not in unique:
            unique.append(role)
    if len(unique) < 2:
        raise ValueError("a huddle needs at least two roles")
    cap = min(max_roles, HUDDLE_MAX_ROLES)
    if len(unique) > cap:
        raise ValueError(f"a huddle holds at most {cap} roles (asked for {len(unique)})")
    if not (topic or "").strip():
        raise ValueError("a huddle needs a topic")

    hid = next_huddle_id(cfg)
    record = {
        "id": hid,
        "topic": topic.strip(),
        "roles": unique,
        "created_at": now_ts(),
        "rounds": 0,
        "turns": [],
    }
    board.log("huddle_started", huddle=hid, roles=unique, topic=record["topic"][:300])

    positions: dict[str, str] = {}
    for role in unique:
        others = [name for name in unique if name != role]
        _, result = run_turn(cfg, role, render_round1(role, others, record["topic"]))
        parsed = parse_position(result.text) if result.exit_code == 0 else {"position": "", "questions": []}
        positions[role] = parsed["position"]
        record["turns"].append({
            "round": 1, "role": role, "exit_code": result.exit_code,
            "position": parsed["position"], "questions": parsed["questions"],
        })
        board.log("huddle_turn", huddle=hid, round=1, role=role, exit_code=result.exit_code,
                  chars=len(parsed["position"]))
    record["rounds"] = 1

    if rounds >= 2 and len(unique) > 1:
        for role in unique:
            _, result = run_turn(cfg, role, render_round2(role, record["topic"], positions))
            parsed = parse_decision(result.text) if result.exit_code == 0 else {"decision": "", "commitments": []}
            record["turns"].append({
                "round": 2, "role": role, "exit_code": result.exit_code,
                "decision": parsed["decision"], "commitments": parsed["commitments"],
            })
            board.log("huddle_turn", huddle=hid, round=2, role=role, exit_code=result.exit_code,
                      chars=len(parsed["decision"]))
        record["rounds"] = 2

    record["finished_at"] = now_ts()
    cfg.huddles_dir.mkdir(parents=True, exist_ok=True)
    (cfg.huddles_dir / f"{hid}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    markdown = render_markdown(record)
    (cfg.huddles_dir / f"{hid}.md").write_text(markdown, encoding="utf-8")
    board.log("huddle_finished", huddle=hid, rounds=record["rounds"], turns=len(record["turns"]))
    record["path"] = str(Path(cfg.huddles_dir) / f"{hid}.md")
    return record


def render_markdown(record: dict) -> str:
    out = [f"# Huddle {record['id']} — {record['topic']}", ""]
    out.append(f"Roles: {', '.join(record['roles'])} · rounds: {record['rounds']} · {record['created_at']}")
    for round_no in (1, 2):
        turns = [t for t in record["turns"] if t["round"] == round_no]
        if not turns:
            continue
        out += ["", f"## Round {round_no}", ""]
        for turn in turns:
            out.append(f"### {turn['role']}  (exit {turn['exit_code']})")
            if round_no == 1:
                out += ["", f"**Position** — {turn['position'] or '(none)'}", ""]
                if turn["questions"]:
                    out.append("**Questions**")
                    out += [f"- {q}" for q in turn["questions"]]
            else:
                out += ["", f"**Decision** — {turn['decision'] or '(none)'}", ""]
                if turn["commitments"]:
                    out.append("**Commitments**")
                    out += [f"- {c}" for c in turn["commitments"]]
            out.append("")
    return "\n".join(out)
