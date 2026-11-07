"""Peer turns: bots talking to bots, with the lead able to read every word.

Why this is a relay and not a socket
------------------------------------
In this deployment a bot *is* a long-lived DSH session, and the only way to put a
turn into someone else's session is `session/prompt` through the harness. So the
tower is the postman. That is not a limitation to hide: it is what makes the
exchange auditable. Every message is filed in `.tower/conversations/` before it is
delivered, so the lead can answer "what did they say to each other" without
reading transcripts - which is exactly the condition the playbook puts on letting
bots talk directly.

Two ways a message is sent
--------------------------
* `tower say backend qa "..."` - a person relays one now.
* a `## Message to <role>` block inside a report - the sender hands the tower the
  message it wants delivered and keeps working; `dispatch` relays it once the
  report is filed.

Neither is a work dispatch: the receiver answers and stops.
"""

from __future__ import annotations

import json

from .adapters import AdapterRequest, get_adapter
from .board import Board, Task, now_ts
from .config import TowerConfig

CHAT_CONTRACT = """## Peer message (a conversation, not a work brief)

You are the **{role}** role. Another role in this tower, **{sender}**, sent you
this message:

> {message}

This is a conversation turn. Answer it; do not start work on it, do not write
deliverables, do not run the report contract.

Answer with exactly these sections:

    ## Reply
    <your answer in your own words, 2-5 sentences>

    ## Escalate
    <one concrete question for the lead, or "none">

The lead files both halves of this exchange; nothing you say here is private.
"""


def render_chat_prompt(role: str, sender: str, message: str) -> str:
    return CHAT_CONTRACT.format(role=role, sender=sender, message=message.strip())


def _sections(text: str) -> dict[str, list[str]]:
    """Small local splitter: the parser in `dispatch` is report-shaped on purpose."""
    from .dispatch import _sections as sections

    return sections(text)


def parse_reply(text: str) -> dict:
    """Read a peer answer. An empty reply is a failed conversation, not silence."""
    from .dispatch import _strip_bullet

    buckets = _sections(text)
    reply = "\n".join(line.rstrip() for line in buckets.get("reply", [])).strip()
    escalate = ""
    for line in buckets.get("escalate", []):
        item = _strip_bullet(line)
        if item and item.lower() not in ("none", "n/a", "-"):
            escalate = item
            break
    return {"reply": reply, "escalate": escalate}


# ------------------------------------------------------------------ storage


def next_conversation_id(cfg: TowerConfig) -> str:
    cfg.conversations_dir.mkdir(parents=True, exist_ok=True)
    highest = 0
    for path in cfg.conversations_dir.glob("C-*.json"):
        try:
            highest = max(highest, int(path.stem.split("-")[1]))
        except (IndexError, ValueError):
            continue
    return f"C-{highest + 1:04d}"


def conversation_path(cfg: TowerConfig, conversation_id: str):
    return cfg.conversations_dir / f"{conversation_id}.json"


def save_conversation(cfg: TowerConfig, record: dict) -> dict:
    cfg.conversations_dir.mkdir(parents=True, exist_ok=True)
    path = conversation_path(cfg, record["id"])
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


def load_conversation(cfg: TowerConfig, conversation_id: str) -> dict:
    path = conversation_path(cfg, conversation_id)
    if not path.exists():
        raise KeyError(f"unknown conversation {conversation_id}")
    return json.loads(path.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ the turn


def run_turn(cfg: TowerConfig, role_name: str, prompt: str, task_id: str = "") -> tuple[object, object]:
    """Run one non-work turn for a role. Returns (adapter, result)."""
    role = cfg.roles[role_name]
    options = dict(cfg.adapter_options.get(role.adapter) or {})
    adapter = get_adapter(role.adapter, options)
    req = AdapterRequest(
        role=role_name,
        prompt=prompt,
        cwd=cfg.role_dir(role_name),
        timeout_s=role.timeout_s,
        env=role.env,
        task_id=task_id,
        options=options,
    )
    return adapter, adapter.run(req)


def say(
    cfg: TowerConfig,
    board: Board,
    sender: str,
    to: str,
    message: str,
    conversation_id: str = "",
    task_id: str = "",
) -> dict:
    """Deliver one peer message and file both halves of the exchange."""
    if sender not in cfg.roles:
        raise KeyError(f"unknown role {sender!r}; known: {', '.join(sorted(cfg.roles))}")
    if to not in cfg.roles:
        raise KeyError(f"unknown role {to!r}; known: {', '.join(sorted(cfg.roles))}")
    if sender == to:
        raise ValueError("a role cannot send a peer message to itself")

    cid = conversation_id or next_conversation_id(cfg)
    record = load_conversation(cfg, cid) if conversation_path(cfg, cid).exists() else {
        "id": cid,
        "created_at": now_ts(),
        "task": task_id,
        "messages": [],
    }
    adapter, result = run_turn(cfg, to, render_chat_prompt(to, sender, message), task_id)
    parsed = parse_reply(result.text) if result.exit_code == 0 else {"reply": "", "escalate": ""}
    entry = {
        "ts": now_ts(),
        "from": sender,
        "to": to,
        "message": message.strip(),
        "reply": parsed["reply"],
        "escalate": parsed["escalate"],
        "adapter": adapter.name,
        "exit_code": result.exit_code,
    }
    record["messages"].append(entry)
    save_conversation(cfg, record)
    board.log(
        "peer_message",
        conversation=cid,
        sender=sender,
        to=to,
        task=task_id,
        chars=len(entry["message"]),
        reply_chars=len(entry["reply"]),
        exit_code=result.exit_code,
    )
    entry["conversation"] = cid
    return entry


def parse_messages(report_text: str) -> list[dict]:
    """Collect `## Message to <role>` blocks a role handed the tower to deliver."""
    out: list[dict] = []
    for heading, lines in _sections(report_text).items():
        if not heading.startswith("message to "):
            continue
        target = heading[len("message to "):].strip()
        body = "\n".join(line.strip() for line in lines if line.strip())
        if target and body:
            out.append({"to": target, "message": body})
    return out


def relay_report_messages(cfg: TowerConfig, board: Board, task: Task) -> list[dict]:
    """Deliver the messages a role put in its report.

    Best effort on purpose: a failed relay must never turn a filed report into a
    failed task. The failure is recorded so the lead sees it instead of it hiding.
    """
    report = task.report or {}
    delivered: list[dict] = []
    for item in parse_messages(report.get("raw", "")):
        target = item["to"]
        if target == task.role or target not in cfg.roles:
            board.log(
                "peer_message_rejected",
                task=task.id,
                sender=task.role,
                to=target,
                reason="self" if target == task.role else "unknown role",
            )
            continue
        try:
            entry = say(cfg, board, task.role, target, item["message"], task_id=task.id)
        except Exception as exc:  # pragma: no cover - defensive
            board.log("peer_message_rejected", task=task.id, sender=task.role, to=target,
                      reason=f"{type(exc).__name__}: {exc}")
            continue
        delivered.append(entry)
    return delivered
