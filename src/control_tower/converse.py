"""Peer turns: bots talking to bots in parallel, with the lead on copy by design.

How a message actually moves
----------------------------
When bot A wants to tell bot B something, A makes **two parallel sends and is
done** — no round trip, no waiting on B:

1. **to the peer** — the tower queues the note straight into B's own session
   (`session/prompt` in queue mode returns the instant the host accepts it);
2. **to the lead** — the same note is filed under `.tower/conversations/` and the
   ledger *before delivery is even attempted*, so 总控 always has the full text.

Delivery is fire-and-forget: `tower say A B "…"` returns as soon as both sends
are handed off. If B is unreachable the failure is recorded on the message —
the record, not the sender, carries the bad news.

How B answers
-------------
In its own time, by the same rule — two parallel sends. From inside its session B
either runs `tower say B A "…"` or adds a `## Message to A` block to its next
report, which `dispatch` relays automatically. A conversation is just the thread
of filed messages; nothing is ever whispered.
"""

from __future__ import annotations

import json

from .adapters import AdapterRequest, get_adapter
from .board import Board, Task, now_ts
from .config import TowerConfig

PEER_NOTE_CONTRACT = """## Peer note from {sender} (not a work brief)

{message}

This is a note from another role in this tower, not an instruction from the
control tower and not a work dispatch. Read it; act on it when it touches your
current work.

If you want to answer, answer in your own time — two parallel sends, same as this
one: run `tower say {to} {sender} "<your answer>"`, or add a
`## Message to {sender}` block to your next report and the tower will relay it.
Everything is filed with the lead either way, so there is nothing to keep private.
"""


def render_peer_note(sender: str, to: str, message: str) -> str:
    return PEER_NOTE_CONTRACT.format(sender=sender, to=to, message=message.strip())


def _sections(text: str) -> dict[str, list[str]]:
    """Report-shaped splitter, borrowed from `dispatch` so one parser owns the format."""
    from .dispatch import _sections as sections

    return sections(text)


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
    """Deliver one peer note: two parallel sends, no waiting on a reply.

    The archive (the lead's copy) is written *first*; delivery into the peer's
    session is attempted second and its outcome is recorded on the message. The
    call returns immediately either way.
    """
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
    entry = {
        "ts": now_ts(),
        "from": sender,
        "to": to,
        "message": message.strip(),
        "task": task_id,
        "delivered": None,      # None = not attempted, True/False = queue outcome
        "detail": "",
    }
    record["messages"].append(entry)
    save_conversation(cfg, record)   # the lead's copy exists before delivery runs

    role = cfg.roles[to]
    options = dict(cfg.adapter_options.get(role.adapter) or {})
    adapter = get_adapter(role.adapter, options)
    req = AdapterRequest(
        role=to,
        prompt=render_peer_note(sender, to, message),
        cwd=cfg.role_dir(to),
        timeout_s=role.timeout_s,
        env=role.env,
        task_id=task_id,
        options=options,
    )
    result = adapter.deliver(req)
    entry["delivered"] = result.exit_code == 0
    entry["detail"] = result.text[:300]
    entry["adapter"] = adapter.name
    save_conversation(cfg, record)
    board.log(
        "peer_message",
        conversation=cid,
        sender=sender,
        to=to,
        task=task_id,
        chars=len(entry["message"]),
        delivered=entry["delivered"],
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
