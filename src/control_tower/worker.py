"""The async queue and its worker: dispatch returns in under a second.

How it works
------------
`tower dispatch --async` (or `dispatch_mode: async`) writes one job file into
`.tower/queue/pending/` and returns the task id immediately. A `tower worker`
process — started automatically on first async dispatch if none is alive —
claims the job, runs the adapter exactly like the sync path (report, relay,
auto-triage), then notifies the tower inbox that the task settled.

Durability is filesystem-only, like every other tower artefact:

* pending → claimed is an atomic `os.replace`, so two workers never run one job;
* a claimed job older than 15 minutes is presumed dead and requeued on startup;
* the worker writes `.tower/queue/worker.json` every cycle as a liveness mark,
  which is also how `ensure_worker` decides whether to spawn a new one.

Relay receipts use the same worker: `tower relay --await-receipt` enqueues a
`relay_watch` job that polls the tower inbox for a reply carrying the relay id
(a peer answers with `tower relay --to tower --reply <id> -m "..."`) and turns
it into a `relay_receipt` inbox notice, or a `relay_timeout` after the budget.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .board import Board, STATUS_ACCEPTED, now_ts
from .config import TowerConfig

HEARTBEAT_STALE_S = 30
CLAIM_STALE_S = 15 * 60
POLL_S = 1.0


def _dirs(cfg: TowerConfig) -> tuple[Path, Path, Path, Path]:
    root = cfg.queue_dir
    pending, claimed, done = root / "pending", root / "claimed", root / "done"
    for path in (root, pending, claimed, done):
        path.mkdir(parents=True, exist_ok=True)
    return root, pending, claimed, done


def enqueue(cfg: TowerConfig, kind: str, task_id: str = "", payload: dict | None = None) -> dict:
    """Write one job and return it. Jobs are ordered by a monotonic counter."""
    _, pending, _, _ = _dirs(cfg)
    seq = 0
    for old in list(pending.glob("*.json")) + list((cfg.queue_dir / "claimed").glob("*.json")):
        try:
            seq = max(seq, int(old.stem.split("-")[1]))
        except (IndexError, ValueError):
            continue
    job = {
        "id": f"Q-{seq + 1:05d}",
        "kind": kind,                       # dispatch | restate | relay_watch
        "task": task_id,
        "payload": payload or {},
        "enqueued_at": now_ts(),
        "attempts": 0,
    }
    (pending / f"{job['id']}.json").write_text(
        json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    return job


def queue_depth(cfg: TowerConfig) -> int:
    _, pending, claimed, _ = _dirs(cfg)
    return len(list(pending.glob("*.json"))) + len(list(claimed.glob("*.json")))


def worker_alive(cfg: TowerConfig) -> bool:
    """True when a heartbeat file is fresh enough to trust."""
    heart = cfg.queue_dir / "worker.json"
    try:
        info = json.loads(heart.read_text(encoding="utf-8"))
        age = time.time() - float(info.get("unix_ts", 0))
        return age < HEARTBEAT_STALE_S
    except (OSError, ValueError, TypeError):
        return False


def ensure_worker(cfg: TowerConfig) -> bool:
    """Start a detached `tower worker` unless a live one is already heartbeating.

    Returns True when a new process was spawned. Works from the CLI (python -m),
    from the frozen console script, and from the DSH bundle's execFile calls.
    """
    if worker_alive(cfg):
        return False
    creationflags = 0
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        creationflags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        kwargs["creationflags"] = creationflags
    else:
        kwargs["start_new_session"] = True
    argv = [sys.executable, "-m", "control_tower", "--workspace", str(cfg.workspace), "worker"]
    try:
        subprocess.Popen(
            argv,
            cwd=str(cfg.workspace),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **kwargs,
        )
        return True
    except OSError:
        return False


def _heartbeat(cfg: TowerConfig) -> None:
    heart = cfg.queue_dir / "worker.json"
    heart.parent.mkdir(parents=True, exist_ok=True)
    heart.write_text(json.dumps({"pid": os.getpid(), "unix_ts": time.time()},
                                ensure_ascii=False), encoding="utf-8")


def _requeue_stale(cfg: TowerConfig) -> int:
    """Move long-claimed jobs back to pending — the worker that held them died."""
    _, pending, claimed, _ = _dirs(cfg)
    moved = 0
    now = time.time()
    for path in claimed.glob("*.json"):
        if now - path.stat().st_mtime > CLAIM_STALE_S:
            dest = pending / path.name
            os.replace(path, dest)
            moved += 1
    return moved


def _claim_next(cfg: TowerConfig) -> tuple[Path, dict] | None:
    _, pending, claimed, _ = _dirs(cfg)
    for path in sorted(pending.glob("*.json")):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            path.rename(path.with_suffix(".bad"))
            continue
        dest = claimed / path.name
        os.replace(path, dest)                    # atomic: one claim per job
        return dest, job
    return None


def _settle_dispatch(cfg: TowerConfig, board: Board, job: dict) -> dict[str, Any]:
    from .converse import relay_report_messages
    from .dispatch import dispatch
    from .triage import redispatch_with_reasons, triage_task

    task = board.load(job["task"])
    dispatch(cfg, board, task)
    relayed = relay_report_messages(cfg, board, task)
    task = board.load(task.id)
    result = triage_task(
        cfg, board, task,
        dispatch_fn=lambda b, t, reasons: redispatch_with_reasons(cfg, b, t, reasons),
    )
    board.notify({
        "event": "async_settled",
        "task": task.id,
        "role": task.role,
        "status": task.status,
        "state": task.state,
        "triage": result.get("outcome", ""),
        "summary": f"async dispatch settled: {task.id} → {task.status}"
                   + (f" (triage: {result.get('outcome')})" if result.get("outcome") else ""),
    })
    board.log("async_done", task=task.id, outcome=result.get("outcome", ""),
              relayed=len(relayed))
    return result


def _settle_restate(cfg: TowerConfig, board: Board, job: dict) -> dict[str, Any]:
    from .dispatch import dispatch_restate

    task = board.load(job["task"])
    dispatch_restate(cfg, board, task)
    task = board.load(task.id)
    where = ("restating — wait for `tower restate ok`"
             if task.status == "restating" else f"gate result: {task.status}")
    board.notify({
        "event": "async_settled",
        "task": task.id,
        "role": task.role,
        "status": task.status,
        "state": task.state,
        "summary": f"async restate settled: {task.id} → {where}",
    })
    board.log("async_done", task=task.id, kind="restate")
    return {"outcome": task.status}


def _watch_relay(cfg: TowerConfig, board: Board, job: dict) -> dict[str, Any]:
    """Poll the tower inbox for a reply carrying this relay id, then notify."""
    payload = job.get("payload") or {}
    relay_id = str(payload.get("relay_id", ""))
    target = str(payload.get("to", ""))
    timeout_s = float(payload.get("timeout_s", 300))
    deadline = time.time() + max(5.0, timeout_s)
    while time.time() < deadline:
        # only an explicit reply (--reply <id>) counts; the tower's own outgoing
        # delivery record also carries the relay id and must not match
        for entry in board.inbox():
            if entry.get("reply") == relay_id:
                summary = ((entry.get("report") or {}).get("summary")
                           or entry.get("message") or "")[:400]
                board.notify({
                    "event": "relay_receipt",
                    "relay": relay_id,
                    "from": entry.get("from", ""),
                    "to": target,
                    "summary": summary,
                })
                board.log("relay_receipt", relay=relay_id, frm=entry.get("from", ""))
                return {"outcome": "receipt"}
        time.sleep(POLL_S)
    board.notify({
        "event": "relay_timeout",
        "relay": relay_id,
        "to": target,
        "summary": f"no receipt for relay {relay_id} within {int(timeout_s)}s",
    })
    board.log("relay_timeout", relay=relay_id)
    return {"outcome": "timeout"}


def run_one_job(cfg: TowerConfig) -> dict[str, Any] | None:
    """Claim and process one job. Returns the outcome dict, or None when empty."""
    pair = _claim_next(cfg)
    if pair is None:
        return None
    claimed_path, job = pair
    board = Board(cfg)
    try:
        if job.get("kind") == "dispatch":
            result = _settle_dispatch(cfg, board, job)
        elif job.get("kind") == "restate":
            result = _settle_restate(cfg, board, job)
        elif job.get("kind") == "relay_watch":
            result = _watch_relay(cfg, board, job)
        else:
            result = {"outcome": "unknown-kind"}
    except FileNotFoundError as exc:
        result = {"outcome": "missing-task", "error": str(exc)}
        board.notify({"event": "async_failed", "task": job.get("task", ""),
                      "summary": f"job {job.get('id')} failed: {exc}"[:300]})
        board.log("async_failed", job=job.get("id"), error=str(exc)[:300])
    except Exception as exc:  # a worker must never die on one bad job
        result = {"outcome": "error", "error": str(exc)[:300]}
        board.notify({"event": "async_failed", "task": job.get("task", ""),
                      "summary": f"job {job.get('id')} failed: {exc}"[:300]})
        board.log("async_failed", job=job.get("id"), error=str(exc)[:300])
    job["settled_at"] = now_ts()
    job["result"] = result
    (cfg.queue_dir / "done" / claimed_path.name).write_text(
        json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    claimed_path.unlink(missing_ok=True)
    return result


def run_worker(cfg: TowerConfig, once: bool = False) -> int:
    """The worker loop. `--once` drains the current queue and exits."""
    _dirs(cfg)
    _requeue_stale(cfg)
    while True:
        _heartbeat(cfg)
        result = run_one_job(cfg)
        if result is None:
            if once:
                return 0
            time.sleep(POLL_S)
            continue
        if once and queue_depth(cfg) == 0:
            return 0
