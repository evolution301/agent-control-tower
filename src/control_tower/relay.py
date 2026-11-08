"""Relay: deliver a message to ANY DSH session, not only tower roles.

Semantics — "dial, hang up, wait for the call back":

* `tower relay --to <target> -m "..."` resolves the target (a session id, a
  title substring, or the reserved alias `tower` meaning this tower's own
  inbox), queues the note, files it in `.tower/relay/`, and returns at once.
* `--await-receipt` enqueues a `relay_watch` job: the worker polls the tower
  inbox for a reply carrying the relay id — the peer answers with
  `tower relay --to tower --reply <id> -m "..."` — and turns it into a
  `relay_receipt` notice, or a `relay_timeout` when the budget runs out.
* Either way the lead reads the outcome from `tower inbox`; the CLI call itself
  never blocks.

The relay contract printed to the peer says exactly that, so a non-tower session
still knows how to call back.
"""

from __future__ import annotations

import json
from typing import Any

from .board import Board, now_ts
from .config import TowerConfig

RELAY_CONTRACT = """## Relayed message from the {tower} control tower

{message}

---
This was delivered by the tower's relay, not by a role. If a reply is expected,
answer from this session with:

    tower relay --to tower --reply {relay_id} -m "<your answer>"

(you can also use `tower relay --to tower --reply {relay_id} -m "..."` from any
machine where the `tower` CLI is installed and pointed at the same workspace).
The tower forwards your answer to whoever sent this.
"""

RESERVED_INBOX = {"tower", "lead", "inbox"}


def relay_dir(cfg: TowerConfig):
    path = cfg.state_dir / "relay"
    path.mkdir(parents=True, exist_ok=True)
    return path


def next_relay_id(cfg: TowerConfig) -> str:
    highest = 0
    for path in relay_dir(cfg).glob("R-*.json"):
        try:
            highest = max(highest, int(path.stem.split("-")[1]))
        except (IndexError, ValueError):
            continue
    return f"R-{highest + 1:04d}"


def _looks_like_session_id(to: str) -> bool:
    """DSH session ids are uuid-shaped; anything else is treated as a title."""
    return len(to) >= 32 and to.count("-") == 4


def resolve_target(cfg: TowerConfig, adapter, to: str) -> tuple[str, str]:
    """Return (session_id, how). The reserved aliases mean the tower's own inbox.

    An explicit session id is used verbatim; anything else is treated as a title
    substring and must match exactly one session, so a fuzzy name can never
    deliver a confidential note into the wrong chat.
    """
    if to.strip().lower() in RESERVED_INBOX:
        return "inbox", "tower-inbox"
    if _looks_like_session_id(to.strip()):
        return to.strip(), "session-id"
    found = adapter.find_session_by_title(to, str(cfg.workspace))
    if found:
        return found, "title-match"
    raise ValueError(
        f"no DSH session whose title contains {to!r}; pass an exact session id, "
        "a unique title substring, or the alias 'tower'"
    )


def send_relay(cfg: TowerConfig, board: Board, adapter, to: str, message: str,
               sender: str = "", reply: str = "", await_receipt: bool = False,
               timeout_s: int = 300) -> dict[str, Any]:
    """Deliver one relayed note and file it. Returns immediately either way."""
    message = (message or "").strip()
    if not message:
        raise ValueError("an empty relay would only waste a turn")
    relay_id = next_relay_id(cfg)
    session_id, how = resolve_target(cfg, adapter, to)

    entry = {
        "id": relay_id,
        "ts": now_ts(),
        "from": sender or cfg.name,
        "to": to,
        "session": session_id,
        "resolution": how,
        "message": message,
        "await_receipt": bool(await_receipt),
        "timeout_s": int(timeout_s),
        "delivered": None,
        "detail": "",
    }

    if session_id == "inbox":
        board.notify({
            "event": "relay_message",
            "relay": relay_id,
            "from": entry["from"],
            "reply": reply,
            "message": message,
            "report": {"summary": message[:400]},
        })
        entry["delivered"] = True
        entry["detail"] = "queued into the tower inbox"
    else:
        text = RELAY_CONTRACT.format(tower=cfg.name, message=message, relay_id=relay_id)
        if reply:
            text = f"(this answers relay {reply})\n\n" + text
        result = adapter.deliver_to_session(session_id, text)
        entry["delivered"] = result.exit_code == 0
        entry["detail"] = result.text[:300]

    relay_dir(cfg).joinpath(f"{relay_id}.json").write_text(
        json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")
    board.log("relay_sent", relay=relay_id, to=to, session=session_id,
              delivered=entry["delivered"], chars=len(message))

    if await_receipt and entry["delivered"]:
        from .worker import enqueue

        enqueue(cfg, "relay_watch", payload={
            "relay_id": relay_id, "to": to, "timeout_s": int(timeout_s)})
        entry["watch"] = "queued"
    return entry
