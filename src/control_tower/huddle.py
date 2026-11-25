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
import threading
import time
from pathlib import Path

from .board import Board, now_ts
from .config import TowerConfig, unknown_role_message
from .converse import run_turn
from .dispatch import _sections

HUDDLE_MAX_ROLES = 6

ROUND1_CONTRACT = """## Huddle round 1 — {topic}

You are the **{role}** role in a huddle with: {others}. The lead opened this room
to settle the question above before anyone goes back to work.

This is a discussion turn. Answer in your own words; do not write deliverables.

Write your answer with exactly these sections, in this reply — not in a task
REPORT file (the adapter's report schema is for work, not for huddles):

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
    # A present role often answers with its usual task report instead of the
    # huddle schema; its position then lives under `## Summary`.
    position = _section(text, "position") or _section(text, "summary")
    questions = _bullets(text, "questions") or _bullets(text, "open issues")
    return {"position": position, "questions": questions}


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


def _collect(cfg: TowerConfig, roles: list[str], prompt_for, timeout: int):
    """Run one round in parallel; a role that misses the deadline never blocks it."""
    boxes: dict[str, dict] = {role: {} for role in roles}
    threads: list[threading.Thread] = []
    for role in roles:
        def call(role=role):
            try:
                _, result = run_turn(cfg, role, prompt_for(role),
                                     expect_report=False, timeout_s=timeout)
                boxes[role]["result"] = result
            except Exception as exc:  # noqa: BLE001 - a dead turn is an absent role
                boxes[role]["error"] = exc
        thread = threading.Thread(target=call, daemon=True)
        thread.start()
        threads.append(thread)
    deadline = time.monotonic() + timeout
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
    absent = [role for role in roles if "result" not in boxes[role]]
    return boxes, absent


def _why_absent(box: dict) -> str:
    """One line a reader can act on: why this role did not answer."""
    exc = box.get("error")
    if exc is not None:
        return f"exception: {exc}"
    result = box.get("result")
    if result is None:
        return "did not answer within the huddle deadline"
    text = (result.text or "").strip().removeprefix("[dsh] ")
    return text or f"exit {result.exit_code}"


def _absent_turn(round_no: int, role: str, box: dict) -> dict:
    reason = _why_absent(box)
    result = box.get("result")
    return {
        "round": round_no,
        "role": role,
        "exit_code": -1 if result is None else result.exit_code,
        "absent": True,
        "reason": reason,
        "meta": dict(result.meta or {}) if result is not None else {},
    }


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
            raise KeyError(unknown_role_message(cfg, role))
        if role not in unique:
            unique.append(role)
    if len(unique) < 2:
        raise ValueError("a huddle needs at least two roles")
    cap = min(max_roles, HUDDLE_MAX_ROLES)
    if len(unique) > cap:
        raise ValueError(f"a huddle holds at most {cap} roles (asked for {len(unique)})")
    if not (topic or "").strip():
        raise ValueError("a huddle needs a topic")

    timeout = max(1, int(getattr(cfg, "huddle_timeout_s", 90)))
    started = time.monotonic()
    hid = next_huddle_id(cfg)
    record = {
        "id": hid,
        "topic": topic.strip(),
        "roles": unique,
        "created_at": now_ts(),
        "rounds": 0,
        "timeout_s": timeout,
        "turns": [],
    }
    board.log("huddle_started", huddle=hid, roles=unique, topic=record["topic"][:300],
              timeout_s=timeout)

    boxes, absent = _collect(
        cfg, unique,
        lambda role: render_round1(role, [n for n in unique if n != role], record["topic"]),
        timeout,
    )
    positions: dict[str, str] = {}
    answered: list[str] = []
    for role in unique:
        result = boxes[role].get("result")
        if result is None or result.exit_code != 0:
            if role not in absent:
                absent.append(role)
            record["turns"].append(_absent_turn(1, role, boxes[role]))
            board.log("huddle_turn", huddle=hid, round=1, role=role, exit_code=-1,
                      absent=True, chars=0)
            continue
        answered.append(role)
        parsed = parse_position(result.text)
        positions[role] = parsed["position"]
        record["turns"].append({
            "round": 1, "role": role, "exit_code": result.exit_code, "absent": False,
            "position": parsed["position"], "questions": parsed["questions"],
            "answer": (result.text or "")[:20000],
            "meta": dict(result.meta or {}),
        })
        board.log("huddle_turn", huddle=hid, round=1, role=role, exit_code=result.exit_code,
                  absent=False, chars=len(parsed["position"]))
    record["rounds"] = 1

    if rounds >= 2 and answered:
        boxes, _ = _collect(
            cfg, answered, lambda role: render_round2(role, record["topic"], positions), timeout,
        )
        for role in answered:
            result = boxes[role].get("result")
            if result is None or result.exit_code != 0:
                absent.append(role)
                record["turns"].append(_absent_turn(2, role, boxes[role]))
                board.log("huddle_turn", huddle=hid, round=2, role=role, exit_code=-1,
                          absent=True, chars=0)
                continue
            parsed = parse_decision(result.text)
            record["turns"].append({
                "round": 2, "role": role, "exit_code": result.exit_code, "absent": False,
                "decision": parsed["decision"], "commitments": parsed["commitments"],
                "answer": (result.text or "")[:20000],
                "meta": dict(result.meta or {}),
            })
            board.log("huddle_turn", huddle=hid, round=2, role=role, exit_code=result.exit_code,
                      absent=False, chars=len(parsed["decision"]))
        record["rounds"] = 2

    absent = [role for role in unique if role in absent]
    if len(absent) == len(unique):
        state = "timeout"
    elif absent:
        state = "partial"
    else:
        state = "done"
    record["discuss"] = {
        "state": state,
        "absent": absent,
        "seconds": int(round(time.monotonic() - started)),
    }
    record["finished_at"] = now_ts()
    cfg.huddles_dir.mkdir(parents=True, exist_ok=True)
    (cfg.huddles_dir / f"{hid}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    markdown = render_markdown(record)
    (cfg.huddles_dir / f"{hid}.md").write_text(markdown, encoding="utf-8")
    board.log("huddle_finished", huddle=hid, rounds=record["rounds"], turns=len(record["turns"]),
              state=state, absent=absent)
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
            if turn.get("absent"):
                reason = turn.get("reason") or f"exceeded {record.get('timeout_s', 90)}s"
                out += [f"### {turn['role']}  (absent)", "",
                        f"**No reply** — {reason}", ""]
                continue
            out.append(f"### {turn['role']}  (exit {turn['exit_code']})")
            answer = str(turn.get("answer") or "")
            if round_no == 1:
                position, questions = turn["position"], turn["questions"]
                if not position and answer:
                    recovered = parse_position(answer)
                    position = recovered["position"]
                    questions = questions or recovered["questions"]
                out += ["", f"**Position** — {position or '(none)'}", ""]
                if questions:
                    out.append("**Questions**")
                    out += [f"- {q}" for q in questions]
            else:
                decision, commitments = turn["decision"], turn["commitments"]
                if not decision and answer:
                    recovered = parse_decision(answer)
                    decision = recovered["decision"]
                    commitments = commitments or recovered["commitments"]
                out += ["", f"**Decision** — {decision or '(none)'}", ""]
                if commitments:
                    out.append("**Commitments**")
                    out += [f"- {c}" for c in commitments]
            out.append("")
    return "\n".join(out)
