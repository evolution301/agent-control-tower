"""Terminal board: `tower board [--watch]`.

Same snapshot as the web panel, printed as text. Works over SSH, in a tmux pane,
or piped into a log. Colour is used only when the output is a TTY.
"""

from __future__ import annotations

import os
import sys
import time

from .board import Board
from .config import TowerConfig
from .snapshot import Snapshot, build_snapshot

RESET = "\033[0m"
DIM = "\033[2m"
BOLD = "\033[1m"
EMBER = "\033[38;5;208m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"

STATE_COLOUR = {"ready": GREEN, "blocked": RED, "failed": RED, "progress": YELLOW}


def _c(text: str, colour: str, enabled: bool) -> str:
    return f"{colour}{text}{RESET}" if enabled else text


def render(snap: Snapshot, colour: bool | None = None, width: int | None = None) -> str:
    if colour is None:
        colour = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
    width = width or min(120, os.get_terminal_size().columns if sys.stdout.isatty() else 120)

    out: list[str] = []
    title = f"{snap.name}"
    out.append(_c("=" * width, DIM, colour))
    out.append(f"{_c(title, BOLD, colour)}  {_c(snap.workspace, DIM, colour)}")
    c = snap.counts
    out.append(
        f"needs you {_c(str(c.get('attention', 0)), EMBER if c.get('attention') else DIM, colour)}"
        f"   in flight {c.get('in_flight', 0)}"
        f"   tasks {c.get('total', 0)}"
        f"   ready {c.get('ready', 0)}"
        f"   {_c(snap.generated_at, DIM, colour)}"
    )
    out.append("")

    out.append(_c("NEEDS YOU", BOLD, colour))
    if snap.attention:
        for t in snap.attention:
            state = t["state"]
            line = f"  {t['id']}  {t['role']:<10} {_c(state, STATE_COLOUR.get(state, DIM), colour):<10}"
            brief = t["brief"][: width - 46]
            out.append(f"{line}  {brief}")
            if t.get("question"):
                out.append(f"       {_c('→ ' + t['question'][: width - 8], EMBER, colour)}")
            out.append(f"       {_c(t['age'] + ' ago · round ' + str(t['round']) + '/' + str(t['max_rounds']), DIM, colour)}")
    else:
        out.append(f"  {_c('nothing — every task is working, idle or accepted', DIM, colour)}")
    out.append("")

    out.append(_c("IN FLIGHT", BOLD, colour))
    if snap.in_flight:
        for t in snap.in_flight:
            out.append(
                f"  {t['id']}  {t['role']:<10} {t['state']:<10} {t['brief'][: width - 46]}"
                f"  {_c(t['age'], DIM, colour)}"
            )
    else:
        out.append(f"  {_c('none', DIM, colour)}")
    out.append("")

    out.append(_c("ROLES", BOLD, colour))
    for r in snap.roles:
        dot = {"working": _c("●", EMBER, colour), "waiting": _c("●", YELLOW, colour)}.get(r["doing"], _c("○", DIM, colour))
        doing = r["doing"]
        task = f"{r['task_id']} {r['brief'][: width - 62]}" if r["task_id"] else "—"
        out.append(f"  {dot} {r['name']:<10} {doing:<8} {task}  {_c(r['age'], DIM, colour)}")
    out.append("")

    out.append(_c("LEDGER", BOLD, colour))
    for e in snap.recent[:6]:
        ts = (e.get("ts") or "").replace("T", " ")[:19]
        out.append(f"  {_c(ts, DIM, colour)}  {_c(e.get('event', ''), EMBER, colour):<16} {e.get('task', '')} {e.get('note', '')[: width - 60]}")
    out.append(_c("=" * width, DIM, colour))
    return "\n".join(out)


def watch(cfg: TowerConfig, interval: float = 2.0) -> None:
    try:
        while True:
            snap = build_snapshot(cfg, Board(cfg))
            frame = render(snap)
            if sys.stdout.isatty():
                sys.stdout.write("\033[2J\033[H")  # clear + home
            print(frame)
            sys.stdout.flush()
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nstopped")
