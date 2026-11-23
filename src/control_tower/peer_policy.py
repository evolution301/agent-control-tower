"""Who may send a peer note, and how many per hour.

The 2026-11-23 incident: three bots started discussing with each other, 181
notes stacked up behind them, and the talking cost more turns than the work it
was about. The tower's job is to keep bots working, so the default is the rule a
company uses — anyone may write the lead, peers may write each other up to a
per-hour quota, and past that the tower stops delivering instead of quietly
burning a turn.

Three modes, set in `tower.toml`:

    [peer_notes]
    mode = "quota"        # default; "lead-only" or "open" also work
    lead = "lead"         # the role that is never rate-limited
    per_hour = 10         # sends per sender per rolling hour (0 = nobody but the lead)

Every decision is a plain dict so a CLI, a tool and a report can all show it:
`{"allow": bool, "reason": str, "mode": str, "sender": str, "to": str,
"count": int, "per_hour": int}`.
"""

from __future__ import annotations

import json
import time

from .config import TowerConfig

WINDOW_S = 3600.0
MODES = ("quota", "lead-only", "open")


def state_path(cfg: TowerConfig):
    return cfg.state_dir / "peer-notes.json"


def _load(cfg: TowerConfig) -> dict:
    path = state_path(cfg)
    if not path.exists():
        return {"senders": {}, "breakers": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"senders": {}, "breakers": {}}
    if not isinstance(data, dict):
        return {"senders": {}, "breakers": {}}
    data.setdefault("senders", {})
    data.setdefault("breakers", {})
    return data


def _save(cfg: TowerConfig, data: dict) -> None:
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    state_path(cfg).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _recent(stamps: list, now: float) -> list[float]:
    return [float(s) for s in stamps if isinstance(s, (int, float)) and now - float(s) < WINDOW_S]


def check(cfg: TowerConfig, sender: str, to: str, now: float | None = None) -> dict:
    """May `sender` write `to` right now? Never mutates state."""
    now = time.time() if now is None else now
    mode = (cfg.peer_note_mode or "quota").strip().lower()
    lead = (cfg.peer_note_lead or "lead").strip()
    per_hour = int(cfg.peer_notes_per_hour)
    data = _load(cfg)
    count = len(_recent(data["senders"].get(sender, []), now))
    verdict = {
        "allow": True,
        "reason": "",
        "mode": mode,
        "sender": sender,
        "to": to,
        "count": count,
        "per_hour": per_hour,
        "lead": lead,
    }
    if mode == "open":
        return verdict
    if mode not in MODES:
        verdict.update(allow=False, reason=f"unknown peer_notes.mode {mode!r}")
        return verdict
    if to == lead:
        verdict["reason"] = "writes to the lead are always delivered"
        return verdict
    if mode == "lead-only":
        verdict.update(allow=False, reason=f"peer_notes.mode=lead-only: only {lead} accepts notes")
        return verdict
    if per_hour <= 0:
        verdict.update(allow=False, reason="peer_notes.per_hour=0: peer notes are disabled")
        return verdict
    if count >= per_hour:
        verdict.update(
            allow=False,
            reason=f"peer-note quota reached ({count}/{per_hour} in the last hour)",
        )
        return verdict
    return verdict


def record(cfg: TowerConfig, sender: str, now: float | None = None) -> None:
    """Count one delivered peer note against `sender`'s rolling hour."""
    now = time.time() if now is None else now
    data = _load(cfg)
    data["senders"][sender] = _recent(data["senders"].get(sender, []), now) + [now]
    _save(cfg, data)


def claim_breaker(cfg: TowerConfig, sender: str, now: float | None = None) -> bool:
    """True when the tower may send `sender` its one throttling notice this hour."""
    now = time.time() if now is None else now
    data = _load(cfg)
    last = data["breakers"].get(sender)
    recent = _recent([last] if last else [], now)
    if recent:
        return False
    data["breakers"][sender] = now
    _save(cfg, data)
    return True


def breaker_text(verdict: dict) -> str:
    return (
        f"[tower] peer note not delivered: {verdict.get('reason') or 'throttled'}. "
        f"Put what you need to say into your next report (## Message to <role>) or wait "
        f"for the next hour window; work briefs are never affected."
    )


def summary(cfg: TowerConfig) -> dict:
    """One line for `tower doctor` / status: mode plus today's busiest senders."""
    data = _load(cfg)
    now = time.time()
    counts = {name: len(_recent(stamps, now)) for name, stamps in data["senders"].items()}
    return {
        "mode": cfg.peer_note_mode,
        "lead": cfg.peer_note_lead,
        "per_hour": cfg.peer_notes_per_hour,
        "senders_last_hour": {k: v for k, v in sorted(counts.items(), key=lambda kv: -kv[1]) if v},
    }
