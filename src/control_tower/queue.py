"""Reading, pruning and purging a live DSH session's queue.

Why this module exists
----------------------
A session queue only ever *grows* from the tower's side: every dispatch and every
peer note is `session/prompt` in `queue` mode, and the session's own turn loop
drains it one turn at a time. When three bots started discussing with each other,
181 messages stacked up behind them (2026-11-23) and every new turn had to read
past the pile. Nothing in the tower ever removed an item, so the queue was a
one-way ratchet.

The API is exact and small: `session/observeState` reports
`queue: [{id, placement, rpcId?, content}]`, `session/updateQueue` removes one
item by id and answers `{"accepted": true}`. Either can be refused — a rejected
call must surface as an exception, never as an empty queue (see `DshRpcError`).
"""

from __future__ import annotations

import json
from typing import Any

# The two shapes a *notification* turn carries. Both are tower bookkeeping, not
# work someone asked a role to do, so they are the first to go under pressure.
PEER_MARK = "not a work brief"
TOWER_MARK = "[tower] "
RELAY_MARK = "relay queued into"
DEFAULT_LIMIT = 20
KINDS = ("all", "peer", "notice")


def text_of(entry: dict) -> str:
    """Flatten a queue item's content blocks into the text a session would read."""
    content = entry.get("content")
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text)
            elif isinstance(block, str) and block.strip():
                parts.append(block)
        if parts:
            return "\n".join(parts)
    return json.dumps(entry.get("content") or "", ensure_ascii=False)


def kind_of(entry: dict) -> str:
    """peer | notice | task — what this queued turn actually is."""
    text = text_of(entry)
    if PEER_MARK in text or text.startswith("## Peer note from "):
        return "peer"
    if TOWER_MARK in text or RELAY_MARK in text:
        return "notice"
    return "task"


def items_from_state(state: dict) -> list[dict]:
    """Normalise `session/observeState` into queue rows the tower can act on."""
    raw = (state or {}).get("queue") or []
    items: list[dict] = []
    if not isinstance(raw, list):
        return items
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        text = text_of(entry)
        items.append(
            {
                "id": entry.get("id"),
                "placement": entry.get("placement") or "queued",
                "rpcId": entry.get("rpcId"),
                "text": text,
                "kind": kind_of(entry),
            }
        )
    return [item for item in items if item["id"]]


def is_notification(item: dict) -> bool:
    return item.get("kind") in ("peer", "notice")


def summarise(item: dict, width: int = 100) -> str:
    text = " ".join(str(item.get("text") or "").split())
    if len(text) <= width:
        return text
    return text[: width - 1] + "…"


def list_items(adapter, session_id: str) -> list[dict]:
    """The session's live queue, via the adapter when it offers one."""
    reader = getattr(adapter, "queue_items", None)
    if callable(reader):
        return list(reader(session_id) or [])
    return items_from_state(adapter.observe_state(session_id))


def _remove(adapter, session_id: str, item: dict) -> str | None:
    """Remove one item; return an error string when the host refused."""
    try:
        adapter.update_queue(session_id, item["id"], "remove")
    except Exception as exc:  # DshRpcError today; a fake adapter may raise anything
        return f"{item['id']}: {exc}"
    return None


def purge(
    adapter,
    session_id: str,
    only: str = "all",
    ids: list[str] | None = None,
    limit: int | None = None,
) -> dict:
    """Remove matching queue items and report the queue before/after.

    `only`: "all" (every item), "peer" (bot-to-bot notes) or "notice" (tower
    generated turns). `limit` stops after that many removals. The result always
    re-reads the queue, so "0 removed" and "cleared" can never be confused.
    """
    kind = (only or "all").strip().lower()
    if kind not in KINDS:
        raise ValueError(f"unknown queue filter {only!r}; expected one of {', '.join(KINDS)}")
    items = list_items(adapter, session_id)
    before = len(items)
    wanted = set(ids or [])
    removed: list[str] = []
    errors: list[str] = []
    for item in items:
        if wanted and item["id"] not in wanted:
            continue
        if not wanted and kind != "all" and item.get("kind") != kind:
            continue
        if limit is not None and len(removed) >= limit:
            break
        error = _remove(adapter, session_id, item)
        if error:
            errors.append(error)
            continue
        removed.append(item["id"])
    try:
        after = len(list_items(adapter, session_id))
    except Exception:
        after = before - len(removed)
    return {
        "session": session_id,
        "filter": kind if not wanted else "ids",
        "queue_before": before,
        "removed": removed,
        "queue_after": after,
        "errors": errors,
    }


def prune(adapter, session_id: str, limit: int = DEFAULT_LIMIT) -> dict:
    """Drop the oldest notification items once the queue passes `limit`.

    Only peer notes and tower notices are candidates: a real dispatch turn is
    never pruned, so the cap can stay low without ever losing work.
    """
    if limit <= 0:
        return {"session": session_id, "queue_before": 0, "pruned": 0, "queue_after": 0}
    items = list_items(adapter, session_id)
    before = len(items)
    if before <= limit:
        return {"session": session_id, "queue_before": before, "pruned": 0,
                "queue_after": before, "pruned_ids": []}
    excess = before - limit
    candidates = [item for item in items if is_notification(item)]
    removed: list[str] = []
    errors: list[str] = []
    for item in candidates[:excess]:
        error = _remove(adapter, session_id, item)
        if error:
            errors.append(error)
            continue
        removed.append(item["id"])
    try:
        after = len(list_items(adapter, session_id))
    except Exception:
        after = before - len(removed)
    result: dict[str, Any] = {
        "session": session_id,
        "queue_before": before,
        "pruned": len(removed),
        "pruned_ids": removed,
        "queue_after": after,
    }
    if errors:
        result["errors"] = errors
    return result


def label(info: dict) -> str:
    """`queue: 23 (auto-pruned 3)` — the one line panels and `tower say` show."""
    if not info:
        return ""
    before = info.get("queue_before")
    after = info.get("queue_after")
    if before is None and after is None:
        return ""
    pruned = int(info.get("pruned") or 0)
    current = after if after is not None else before
    return "queue: %s (auto-pruned %d)" % (current, pruned) if pruned else "queue: %s" % current
