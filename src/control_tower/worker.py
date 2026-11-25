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
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from .board import Board, STATUS_ACCEPTED, STATUS_FAILED, STATUS_REWORK, now_ts
from .config import TowerConfig, reload_config
from .locks import release_lock, tower_lock, try_lock

HEARTBEAT_STALE_S = 30
CLAIM_STALE_S = 15 * 60
POLL_S = 1.0
# A review task is done once it has a verdict: `accepted` on pass, `closed` when
# the verdict reworks the origin (the rework lives on the origin, not on the review).
REVIEW_CLOSED = "closed"


def _dirs(cfg: TowerConfig) -> tuple[Path, Path, Path, Path]:
    root = cfg.queue_dir
    pending, claimed, done = root / "pending", root / "claimed", root / "done"
    for path in (root, pending, claimed, done):
        path.mkdir(parents=True, exist_ok=True)
    return root, pending, claimed, done


def wait_for_worker(cfg: TowerConfig, timeout_s: float = 6.0, poll_s: float = 0.25) -> bool:
    """Give a just-spawned worker a moment to write its first heartbeat.

    `ensure_worker` returns as soon as the detached process is created; the
    heartbeat lands ~1s later. Callers that must report "nothing is consuming
    this queue" poll briefly instead of racing the spawn.
    """
    deadline = time.time() + max(0.0, timeout_s)
    while True:
        if worker_alive(cfg):
            return True
        if time.time() >= deadline:
            return False
        time.sleep(max(0.05, poll_s))


def _inflight_jobs(info: dict) -> list[dict]:
    """Every job one worker process has claimed and not finished, oldest first."""
    raw = info.get("jobs")
    if not isinstance(raw, dict):
        # a 0.18.3 worker kept one flat job at the top level
        job_id = str(info.get("current_job") or "")
        raw = ({job_id: {"kind": info.get("current_kind"), "task": info.get("task"),
                         "started_at": info.get("started_at")}} if job_id else {})
    now = time.time()
    jobs = []
    for job_id, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        started = float(entry.get("started_at") or 0)
        jobs.append({
            "id": str(job_id),
            "kind": str(entry.get("kind") or ""),
            "task": str(entry.get("task") or ""),
            "role": str(entry.get("role") or ""),
            "age_minutes": round((now - started) / 60, 1) if started else None,
        })
    jobs.sort(key=lambda item: item["age_minutes"] or 0.0, reverse=True)
    return jobs


def worker_status(cfg: TowerConfig) -> dict:
    """Live snapshot of the async consumer for this workspace.

    The 2026-11-10 incident: adversarial jobs piled up for 33 hours because the
    only worker process belonged to another workspace. This is what makes that
    visible before a human has to notice the silence.
    """
    from . import __version__

    _, pending, claimed, done = _dirs(cfg)
    info: dict = {}
    try:
        info = json.loads((cfg.queue_dir / "worker.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        info = {}
    jobs = _inflight_jobs(info)
    current = jobs[0] if jobs else {}
    holder = _lock_holder(cfg)
    age = time.time() - float(info.get("unix_ts", 0) or 0)
    oldest_minutes = 0.0
    files = sorted(pending.glob("*.json"), key=lambda f: f.stat().st_mtime)
    if files:
        oldest_minutes = round((time.time() - files[0].stat().st_mtime) / 60, 1)
    return {
        "workspace": str(cfg.workspace),
        "alive": bool(info) and age < HEARTBEAT_STALE_S,
        "pid": info.get("pid"),
        "version": info.get("v", ""),
        "heartbeat_age_s": round(age, 1) if info else None,
        "pending": len(list(pending.glob("*.json"))),
        "claimed": len(list(claimed.glob("*.json"))),
        "done": len(list(done.glob("*.json"))),
        "oldest_pending_minutes": oldest_minutes,
        "version_matches": info.get("v") == __version__ if info else False,
        "current_job": current.get("id", ""),
        "current_kind": current.get("kind", ""),
        "current_task": current.get("task", ""),
        "current_age_minutes": current.get("age_minutes"),
        "jobs": jobs,
        "pool": int(info.get("pool") or 0),
        # a heartbeat from a process that is not the lock holder is a stale
        # singleton: it may claim work, but something else owns the slot
        "pid_holds_lock": bool(info) and lock_is_held(cfg)
                          and str(info.get("pid") or "") == str(holder.get("pid") or ""),
        "lock_holder": holder,
    }


def backlog_warning(cfg: TowerConfig) -> str | None:
    """One-line alarm when queued work has no consumer (or has waited too long)."""
    status = worker_status(cfg)
    if not status["pending"]:
        return None
    limit = float(getattr(cfg, "queue_alert_minutes", 15) or 15)
    if not status["alive"]:
        return (f"⚠ queue backlog: {status['pending']} job(s) pending, NO worker alive "
                f"for {cfg.workspace} — run `tower worker --status`, then "
                f"`tower worker` (or any dispatch/review auto-starts one)")
    if status["oldest_pending_minutes"] > limit:
        return (f"⚠ queue backlog: oldest pending job is "
                f"{status['oldest_pending_minutes']:.0f} min old (>{limit:.0f} min) "
                f"while a worker is alive — check `tower worker --status` / logs")
    return None


def _task_role(cfg: TowerConfig, task_id: str) -> str:
    try:
        return str(Board(cfg).load(str(task_id or "")).role or "")
    except (OSError, ValueError, KeyError):     # no task file: nothing to serialise
        return ""


def _job_role(cfg: TowerConfig, job: dict) -> str:
    """Which role's session this job drives: payload first, task file second.

    `serial_requests` never engaged for async work because nobody filled
    payload.role; the task file is the record that always has it.
    """
    role = str((job.get("payload") or {}).get("role") or "")
    return role or _task_role(cfg, job.get("task", ""))


def enqueue(cfg: TowerConfig, kind: str, task_id: str = "", payload: dict | None = None) -> dict:
    """Write one job and return it. Jobs are ordered by a monotonic counter.

    Concurrency-safe: the whole pick-id → write → replace section runs under the
    in-process claim lock, and a cross-process `os.replace` collision retries with
    a bumped id instead of clobbering an existing job.
    """
    payload = dict(payload or {})
    if not payload.get("role") and task_id:
        payload["role"] = _task_role(cfg, task_id)
    with _CLAIM_LOCK:
        _, pending, _, _ = _dirs(cfg)
        seq = 0
        for old in list(pending.glob("*.json")) + list((cfg.queue_dir / "claimed").glob("*.json")) \
                + list((cfg.queue_dir / "done").glob("*.json")):
            try:
                seq = max(seq, int(old.stem.split("-")[1]))
            except (IndexError, ValueError):
                continue
        job = {
            "id": f"Q-{seq + 1:05d}",
            "kind": kind,                       # dispatch | restate | relay_watch
            "task": task_id,
            "payload": payload,
            "enqueued_at": now_ts(),
            "attempts": 0,
        }
        dest = pending / f"{job['id']}.json"
        tmp = pending / f"{job['id']}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp"
        tmp.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        for attempt in range(5):
            try:
                os.replace(tmp, dest)
                break
            except OSError:
                if attempt == 4:
                    tmp.unlink(missing_ok=True)
                    raise
                seq += 1
                job["id"] = f"Q-{seq + 1:05d}"
                dest = pending / f"{job['id']}.json"
                tmp = pending / f"{job['id']}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp"
                tmp.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    board = Board(cfg)
    board.event("enqueued", job=job["id"], kind=kind, task=task_id)
    # a queued job must never sit without a consumer (2026-11-10 adversarial pile-up)
    try:
        job["spawned"] = bool(ensure_worker(cfg))
    except (OSError, RuntimeError):
        job["spawned"] = False
    return job
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

    A heartbeat whose recorded version differs from the running code (a CLI/
    plugin update while the worker stayed up) is treated as stale: the old
    process is terminated and a fresh one takes over. Returns True when a new
    process was spawned. Works from the CLI (python -m), from the frozen console
    script, and from the DSH bundle's execFile calls.
    """
    from . import __version__

    heart = cfg.queue_dir / "worker.json"
    if worker_alive(cfg):
        try:
            info = json.loads(heart.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            info = {}
        if info.get("v") == __version__ and info.get("cfg_sha") in (None, _config_hash(cfg)):
            return False
        # code moved under a live worker: retire it and take over
        pid = int(info.get("pid", 0) or 0)
        if pid and pid != os.getpid():
            try:
                os.kill(pid, 9)
            except OSError:
                pass
        try:
            heart.unlink()
        except OSError:
            pass
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


def _config_hash(cfg: TowerConfig) -> str:
    """B1: the worker must never dispatch with a stale tower.toml."""
    import hashlib

    path = Path(getattr(cfg, "config_path", "") or "")
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return ""


_HEART_LOCK = threading.Lock()
_POOL = 0


def _lock_path(cfg: TowerConfig) -> Path:
    return cfg.state_dir / "worker.lock"


def _owner_path(cfg: TowerConfig) -> Path:
    # Windows locks deny reads of the locked byte range, so the pid lives beside
    # the lock file rather than inside it.
    return cfg.state_dir / "worker.owner"


def _lock_holder(cfg: TowerConfig) -> dict:
    """Whoever took the consumer slot last."""
    try:
        info = json.loads(_owner_path(cfg).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return info if isinstance(info, dict) else {}


def lock_is_held(cfg: TowerConfig) -> bool:
    """Is the consumer slot actually taken? A dead holder leaves only its text."""
    path = _lock_path(cfg)
    if not path.exists():
        return False
    fh = try_lock(path)
    if fh is None:
        return True
    release_lock(fh)
    return False


def _take_worker_lock(cfg: TowerConfig):
    """Claim this workspace's consumer slot: the open handle, or None when taken."""
    from . import __version__

    fh = try_lock(_lock_path(cfg))
    if fh is None:
        return None
    holder = {"pid": os.getpid(), "started": now_ts(), "version": __version__}
    _owner_path(cfg).write_text(json.dumps(holder, ensure_ascii=False), encoding="utf-8")
    return fh


def _heartbeat_jobs(cfg: TowerConfig) -> dict[str, dict]:
    try:
        info = json.loads((cfg.queue_dir / "worker.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    raw = info.get("jobs")
    if isinstance(raw, dict):
        return {str(key): dict(value) for key, value in raw.items() if isinstance(value, dict)}
    job_id = str(info.get("current_job") or "")
    if not job_id:
        return {}
    return {job_id: {"kind": str(info.get("current_kind") or ""),
                     "task": str(info.get("task") or ""),
                     "started_at": float(info.get("started_at") or 0)}}


def _write_heartbeat(cfg: TowerConfig, jobs: dict[str, dict]) -> None:
    from . import __version__

    info: dict[str, Any] = {"pid": os.getpid(), "unix_ts": time.time(),
                            "v": __version__, "cfg_sha": _config_hash(cfg),
                            "pool": _POOL, "jobs": jobs}
    if len(jobs) == 1:                  # keep the one-job view `tower doctor` prints
        job_id, entry = next(iter(jobs.items()))
        info.update({"current_job": job_id,
                     "current_kind": str(entry.get("kind") or ""),
                     "started_at": float(entry.get("started_at") or time.time()),
                     "task": str(entry.get("task") or "")})
    heart = cfg.queue_dir / "worker.json"
    heart.parent.mkdir(parents=True, exist_ok=True)
    heart.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")


def _heartbeat(cfg: TowerConfig, job: dict | None = None) -> None:
    """Refresh liveness, merging this job into the map of in-flight jobs.

    Every pool thread writes heartbeats; merging is what stops the supervisor's
    bare call from erasing the job a worker thread is actually running.
    """
    with _HEART_LOCK:
        jobs = _heartbeat_jobs(cfg)
        if job:
            jobs[str(job.get("id") or "")] = {
                "kind": str(job.get("kind") or ""),
                "task": str(job.get("task") or ""),
                "role": str(job.get("role") or ""),
                "started_at": float(job.get("started_ts") or time.time()),
            }
        _write_heartbeat(cfg, jobs)


def _clear_heartbeat(cfg: TowerConfig, job_id: str) -> None:
    """Drop just this job; the pool's other threads keep theirs."""
    with _HEART_LOCK:
        jobs = _heartbeat_jobs(cfg)
        if jobs.pop(str(job_id or ""), None) is not None:
            _write_heartbeat(cfg, jobs)


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


_CLAIM_LOCK = threading.Lock()   # in-process claim guard; os.replace covers cross-process


def _claim_next(cfg: TowerConfig) -> tuple[Path, dict] | None:
    _, pending, claimed, _ = _dirs(cfg)
    with _CLAIM_LOCK:
        for path in sorted(pending.glob("*.json")):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                path.rename(path.with_suffix(".bad"))
                continue
            except OSError:
                continue                     # another worker claimed it mid-read
            dest = claimed / path.name
            try:
                os.replace(path, dest)       # atomic: one claim per job
            except OSError:
                continue                     # claimed elsewhere between read and move
            return dest, job
    return None


def _settlement_notice(cfg: TowerConfig, task, result: dict) -> dict[str, Any]:
    """The lead notice: verdict line, report counts, optional message block."""
    report = task.report or {}
    summary = str(report.get("summary") or "").replace("\n", " ")[:200]
    lines = [
        f"[tower] {task.id} settled: {task.status}"
        + (f" (triage: {result.get('outcome')})" if result.get("outcome") else "")
        + f" | role={task.role} | summary={summary}",
        f"report: {len(report.get('files') or [])} files, "
        f"{len(report.get('checks') or [])} checks",
    ]
    lead = _lead_role(cfg)
    if lead:
        from .converse import parse_messages

        for item in parse_messages(str(report.get("raw") or "")):
            if item["to"] == lead:
                lines.extend([f"## Message to {lead}", item["message"][:400]])
                break
    return {
        "event": "async_settled",
        "task": task.id,
        "role": task.role,
        "status": task.status,
        "state": task.state,
        "triage": result.get("outcome", ""),
        "summary": "\n".join(lines),
    }


def _lead_role(cfg: TowerConfig) -> str:
    """The role settlements are addressed to, or "" when the tower has none."""
    from . import org

    lead = org.lead(cfg)
    return lead if lead in cfg.roles else ""


def lead_push_target(cfg: TowerConfig) -> tuple[str, str]:
    """`(session_id, reason)` — where a settlement push should go.

    A `lead_session` notice uses the configured session. An `inbox` tower still
    pushes when it has a lead with a DSH session pinned; otherwise the reason
    explains why the settlement stayed in `.tower/inbox`.
    """
    if cfg.notify_kind == "lead_session":
        if cfg.notify_session:
            return cfg.notify_session, ""
        return "", "notify.kind=lead_session without notify.session"
    if cfg.notify_kind != "inbox":
        return "", f"notify.kind={cfg.notify_kind} (push disabled)"
    lead = _lead_role(cfg)
    if not lead:
        from . import org

        return "", f"no lead role (org.lead={org.lead(cfg)!r})"
    sessions = dict((cfg.adapter_options.get("dsh") or {}).get("sessions") or {})
    session = str(sessions.get(lead) or "").strip()
    if not session:
        return "", f"no dsh session pinned for lead {lead!r}"
    return session, ""


def _deliver_lead_push(cfg: TowerConfig, board: Board, task, text: str) -> bool:
    """Push one settlement message to the target from `lead_push_target()`.

    Uses the settled task's own adapter (so mock towers work in tests and dsh
    towers in production). Failures never break the settlement: the inbox entry
    is the durable record, the session push is best-effort.
    """
    session, reason = lead_push_target(cfg)
    if not session:
        board.log("lead_push_skipped", task=task.id, role=task.role, reason=reason)
        return False
    try:
        from .adapters import get_adapter

        role = cfg.roles.get(task.role)
        adapter_name = role.adapter if role else "dsh"
        options = dict(cfg.adapter_options.get(adapter_name) or {})
        adapter = get_adapter(adapter_name, options)
        result = adapter.deliver_to_session(session, text)
        return result.exit_code == 0
    except Exception as exc:
        board.log("lead_push_skipped", task=task.id, role=task.role,
                  reason=f"{type(exc).__name__}: {exc}")
        return False


def stage_rework(board: Board, task, reasons: str, staged: list) -> None:
    """Record the rework verdict + reasons WITHOUT dispatching (lock stays short).

    The caller dispatches the staged task after releasing `tower_lock`, so the
    minutes-long adapter turn never runs under the board lock.
    """
    task.brief = (task.brief + "\n\n## Rework: the previous attempt failed verification\n"
                  + reasons)[:8000]
    board.save(task)
    staged.append(task)


def cancel_jobs(cfg: TowerConfig, task_id: str, except_job: str = "") -> int:
    """Drop queued jobs for a task that was closed while they waited."""
    _, pending, _, done_dir = _dirs(cfg)
    dropped = 0
    for path in sorted(pending.glob("*.json")):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if job.get("task") != task_id or job.get("id") == except_job:
            continue
        job["settled_at"] = now_ts()
        job["result"] = {"outcome": "cancelled", "reason": "task already closed"}
        (done_dir / path.name).write_text(json.dumps(job, indent=2), encoding="utf-8")
        path.unlink(missing_ok=True)
        dropped += 1
    if dropped:
        Board(cfg).log("jobs_cancelled", task=task_id, count=dropped)
    return dropped


def _settle_ignored(cfg: TowerConfig, board: Board, job: dict, task) -> dict[str, Any]:
    """A settlement that arrives after a person closed the task.

    The acceptance record wins: no triage, no rework, no status flip, no
    settled_at. The notice still goes out, marked informational, and queued jobs
    for the same task are cancelled so a second settlement cannot resurrect it.
    """
    board.log("settle_ignored", job=job.get("id"), task=task.id, status=task.status,
              note=f"settle_ignored: task already {task.status}")
    board.notify({"event": "async_settled", "task": task.id, "status": task.status,
                  "summary": f"[tower] {task.id} is already {task.status} — late "
                             "settlement ignored (informational)"})
    cancel_jobs(cfg, task.id, except_job=job.get("id", ""))
    return {"outcome": "ignored", "status": task.status}


def _settle_dispatch(cfg: TowerConfig, board: Board, job: dict) -> dict[str, Any]:
    from .converse import relay_report_messages
    from .dispatch import LATE_STATUS, closed_task, dispatch, is_progress_report
    from .triage import redispatch_with_reasons, triage_task

    task = board.load(job["task"])
    if not task.mode:
        task.mode = "async"
        board.save(task)
    if closed_task(board, task.id) is not None:
        return _settle_ignored(cfg, board, job, task)
    # adapter turn runs OUTSIDE the tower lock (a dsh turn can take minutes —
    # holding the lock here starved every other dispatch: Q-00002, 2026-11-09)
    dispatch(cfg, board, task)
    task = board.load(task.id)
    if closed_task(board, task.id) is not None:
        return _settle_ignored(cfg, board, job, task)
    if task.status == LATE_STATUS:
        # P1-C: a timed-out turn is still running. Settling it here would stamp a
        # verdict on work nobody has read yet; the late tick owns the decision.
        board.log("async_late", task=task.id,
                  hard_deadline=(task.escalation.get("late") or {}).get("hard_deadline", ""))
        board.notify({"event": "async_late", "task": task.id,
                      "summary": f"[tower] {task.id} timed out but the adapter is still "
                                 "running — supervise will re-check"})
        return {"outcome": "late", "task": task.id}
    if is_progress_report(task.report):
        # 2026-11-24: a progress report keeps the task where it is. Settling it
        # (or handing it to triage) would fail and rework a lane that is simply
        # still working — the very thing this signal exists to prevent.
        board.log("async_progress", task=task.id, seen=(task.escalation.get("progress") or {}).get("seen", 0))
        board.notify({"event": "async_progress", "task": task.id,
                      "summary": f"[tower] {task.id} still running — progress report filed"})
        return {"outcome": "progress", "task": task.id}
    relay_report_messages(cfg, board, task)
    staged = []
    with tower_lock(cfg):
        task = board.load(task.id)
        result = triage_task(
            cfg, board, task,
            # stage the rework under the lock; the redispatch runs outside it
            dispatch_fn=lambda b, t, reasons: stage_rework(b, t, reasons, staged),
        )
        task = board.load(task.id)
        if task.status == "accepted":
            reflect_skill_draft(cfg, board, task)
        task.settled_at = now_ts()
        notice = _settlement_notice(cfg, task, result)
        board.notify(notice)
        task.notified = _deliver_lead_push(cfg, board, task, notice["summary"])
        board.save(task)
    if staged:
        dispatch(cfg, board, staged[0])
        relay_report_messages(cfg, board, staged[0])
    board.event("settled", task=task.id, status=task.status,
                triage=result.get("outcome", ""))
    board.event("notified", task=task.id, inbox=True, lead_session=task.notified)
    board.log("async_done", task=task.id, outcome=result.get("outcome", ""))
    return result


def _review_missing(cfg: TowerConfig, board: Board, origin_id: str,
                    review_task, reviewer: str) -> dict[str, Any]:
    """No reviewer report means no verdict — an empty report never accepts the origin.

    T-0045: the adversarial pass used to accept the origin when the reviewer's turn
    had died, because zero findings was read as "clean". The origin stays `reported`
    (so the lead queue still carries it) and the review task is closed as failed.
    """
    with tower_lock(cfg):
        origin = board.load(origin_id)
        origin.review = {"rounds": int((origin.review or {}).get("rounds", 0)),
                         "verdict": "unreviewed", "reviewer": reviewer,
                         "findings": 0, "proved": 0, "review_task": review_task.id}
        board.transition(review_task, STATUS_FAILED,
                         note=f"review closed: no report for {origin_id}")
        notice = {
            "event": "review_missing",
            "task": origin_id,
            "reviewer": reviewer,
            "summary": (f"[tower] {origin_id} review produced no report "
                        f"({review_task.id} status={review_task.status}) — "
                        f"the task is still reported; review it again by hand"),
        }
        board.notify(notice)
        origin.notified = _deliver_lead_push(cfg, board, origin, notice["summary"])
        board.save(origin)
    board.event("adversarial_missing", origin=origin_id, review=review_task.id)
    board.log("adversarial_missing", origin=origin_id, review=review_task.id,
              reviewer=reviewer)
    return {"outcome": "unreviewed", "findings": 0, "proved": 0,
            "unproved": 0, "dropped": 0, "out_of_scope": 0}


def _run_adversarial(cfg: TowerConfig, board: Board, job: dict) -> dict[str, Any]:
    """One independent "break it" pass over a ready task (pstack thermos/interrogate).

    The reviewer's findings only count when they carry a run's proof (command →
    output marker, same bar as Checks). Proved findings rework the origin task
    with the evidence attached; zero proved findings accept it.
    """
    from .converse import relay_report_messages
    from .dispatch import COMMAND_MARKER, FINDINGS_HEADING, PROOF_MARKER, dispatch
    from .verify import verify_report

    origin_id = job["task"]
    reviewer = str((job.get("payload") or {}).get("reviewer", ""))
    origin = board.load(origin_id)

    review_task = board.create(
        reviewer,
        f"[adversarial review of {origin_id}] Break this change.\n\n"
        f"Scope: ONLY what the report and files below added or changed.\n"
        f"Write your report with exactly these sections:\n"
        f"## Status\n"
        f"{FINDINGS_HEADING}\n"
        f"One line per finding, or `- none` when clean:\n"
        f"- <what is wrong> | command: <cmd> → output: <the output that shows it>\n"
        f"## Files\n"
        f"## Checks\n"
        f"## Open issues\n"
        f"## Self-review\n"
        f"Leave out anything you did not run. Style is out of scope.\n"
        f"No findings is a valid result — write `- none` under ## Findings. "
        f"Never misreport severity.\n\n"
        f"Origin brief:\n{origin.brief[:1500]}\n\n"
        f"Origin report summary: {(origin.report or {}).get('summary', '')[:400]}\n"
        f"Origin files: {', '.join((origin.report or {}).get('files', [])[:12])}",
    )
    review_task.review_of = origin_id
    board.save(review_task)
    board.event("adversarial_started", origin=origin_id, review=review_task.id,
                reviewer=reviewer)

    dispatch(cfg, board, review_task)
    relay_report_messages(cfg, board, review_task)
    review_task = board.load(review_task.id)

    raw = review_task.report or {}
    if not any(raw.get(k) for k in ("summary", "findings", "files", "checks")):
        return _review_missing(cfg, board, origin_id, review_task, reviewer)
    findings = [str(f).strip() for f in (raw.get("findings") or []) if str(f).strip()]
    rounds = int((origin.review or {}).get("rounds", 0)) + 1

    # ---- reviewer governance (pstack no-comments: the auditor is audited) ----
    origin_paths = set(origin.report.get("files") or []) if origin.report else set()
    path_re = re.compile(r"[A-Za-z0-9_\-.]+\.(?:py|md|js|ts|txt|json|html|css|toml|ya?ml)")
    proved, dropped, out_of_scope, unproved = [], [], [], []
    for f in findings:
        paths = set(path_re.findall(f))
        escapes = {p2 for p2 in paths if origin_paths and not any(
            p2 == op or p2.endswith("/" + op) or op.endswith("/" + p2) for op in origin_paths)}
        has_run = ("command:" in f or "`" in f) and (
            "→" in f or "->" in f or "output:" in f)
        if not has_run:
            if COMMAND_MARKER in f and not PROOF_MARKER.search(f):
                # names a command but never shows its output: visible, not blocking
                unproved.append(f)
            else:
                dropped.append(f)
        elif escapes:
            out_of_scope.append(f)
        else:
            proved.append(f)
    rejected = len(dropped) + len(out_of_scope)
    rep = update_reputation(cfg, reviewer, proved=len(proved),
                            dropped=len(dropped), out_of_scope=len(out_of_scope))

    # two audited-away findings void the report: rerun once with the failure named;
    # a second void report is reported open instead of being trusted again
    if rejected >= 2 and not (job.get("payload") or {}).get("retry"):
        from .worker import enqueue as _enq

        _enq(cfg, "adversarial", job["task"], payload={
            "reviewer": reviewer, "retry": True,
            "failure": f"previous report voided: {rejected} findings failed the audit "
                       f"(no repro / out of scope) — rerun with evidence only",
        })
        board.event("review_voided", origin=origin_id, reviewer=reviewer,
                    rejected=rejected, rerun=True)
        board.transition(review_task, REVIEW_CLOSED,
                         note=f"review voided: rerun requested for {origin_id}")
        return {"outcome": "reviewer_rerun", "rejected": rejected}
    if rejected >= 2:
        board.notify({"event": "review_open_issue", "task": origin_id,
                      "reviewer": reviewer,
                      "summary": f"reviewer {reviewer} failed the audit twice on "
                                 f"{origin_id}; findings discarded — escalate manually"})
        board.log("review_open_issue", origin=origin_id, reviewer=reviewer)

    with tower_lock(cfg):
        origin = board.load(origin_id)
        verdict = "pass"
        staged_rework = []
        if proved:
            verdict = "rework"
            stage_rework(board, origin, "; ".join(proved)[:400], staged_rework)
        elif rejected and not proved:
            # nothing survived the audit: treat the report as noise and let the
            # mechanical verifier decide the origin's fate
            verdict = "pass"
            if verify_report(cfg, origin).ok:
                board.transition(origin, STATUS_ACCEPTED,
                                 note=f"review noise dropped ({rejected} findings failed audit)",
                                 verification=verify_report(cfg, origin).as_dict())
            else:
                verdict = "rework"
                stage_rework(board, origin, "review passed but verification regressed",
                             staged_rework)
        elif not verify_report(cfg, origin).ok:
            verdict = "rework"
            stage_rework(board, origin, "review pass: verification regressed", staged_rework)
        else:
            board.transition(origin, STATUS_ACCEPTED,
                             note=f"adversarial review passed ({review_task.id})",
                             verification=verify_report(cfg, origin).as_dict())
        if staged_rework:
            board.transition(origin, STATUS_REWORK,
                             note=f"rework: review ({len(proved)} proved)")
        reflect_skill_draft(cfg, board, origin)
        origin.review = {"rounds": rounds, "verdict": verdict, "reviewer": reviewer,
                         "findings": len(findings), "proved": len(proved),
                         "unproved": len(unproved), "dropped": len(dropped),
                         "out_of_scope": len(out_of_scope),
                         "findings_section": bool(raw.get("findings_section", True)),
                         "findings_fallback": bool(raw.get("findings_fallback", False)),
                         "review_task": review_task.id}
        board.transition(review_task, STATUS_ACCEPTED if verdict == "pass" else REVIEW_CLOSED,
                         note=f"review closed: {verdict} on {origin_id}")
        notice = {
            "event": "review_verdict",
            "task": origin_id,
            "reviewer": reviewer,
            "verdict": verdict,
            "summary": (f"[tower] {origin_id} review verdict: {verdict} | role={origin.role} "
                        f"| findings={len(findings)} proved={len(proved)} "
                        f"unproved={len(unproved)} "
                        f"| reviewer={reviewer} ({review_task.id})"),
        }
        board.notify(notice)
        origin.notified = _deliver_lead_push(cfg, board, origin, notice["summary"])
        origin.settled_at = now_ts()
        board.save(origin)
    if staged_rework:
        dispatch(cfg, board, staged_rework[0])
        relay_report_messages(cfg, board, staged_rework[0])
    board.event("adversarial_verdict", origin=origin_id, review=review_task.id,
                verdict=verdict, findings=len(findings), proved=len(proved),
                unproved=len(unproved), dropped=len(dropped),
                out_of_scope=len(out_of_scope), reputation=rep.get("score"))
    board.log("adversarial_done", origin=origin_id, review=review_task.id,
              verdict=verdict, proved=len(proved), unproved=len(unproved))
    return {"outcome": verdict, "findings": len(findings), "proved": len(proved),
            "unproved": len(unproved), "dropped": len(dropped),
            "out_of_scope": len(out_of_scope)}


def _restate_budget(cfg: TowerConfig, task) -> float:
    role = cfg.roles.get(task.role)
    return max(1.0, float(getattr(role, "timeout_s", 1800) or 1800))


def _restate_target_problem(cfg: TowerConfig, task) -> str:
    """Why the restate target cannot answer, or "" when it looks usable."""
    role = cfg.roles.get(task.role)
    if role is None:
        return f"unknown role {task.role!r}"
    options = dict(cfg.adapter_options.get(role.adapter) or {})
    session = str((options.get("sessions") or {}).get(task.role) or "").strip()
    if not session or session == "auto":
        return ""
    from .adapters import get_adapter

    check = getattr(get_adapter(role.adapter, options), "session_state", None)
    if not callable(check):
        return ""
    state = check(session) or {}
    if state.get("error"):
        return f"projections unavailable: {state['error']}"
    if not state.get("model_known"):
        return f"session {session} has no model projection"
    if not state.get("model"):
        return f"session {session} has no model selected"
    return ""


class _MutedBoard:
    """Board proxy the restate thread loses the right to write through on timeout."""

    def __init__(self, board: Board) -> None:
        self._board = board
        self._muted = threading.Event()

    def mute(self) -> None:
        self._muted.set()

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._board, name)
        if not callable(attr):
            return attr

        def call(*args: Any, **kwargs: Any) -> Any:
            if self._muted.is_set():
                return None
            return attr(*args, **kwargs)

        return call


def _restate_failed(cfg: TowerConfig, board: Board, task,
                    event: str, reason: str) -> dict[str, Any]:
    with tower_lock(cfg):
        task = board.load(task.id)
        board.transition(task, STATUS_FAILED, note=f"{event}: {reason}"[:500])
        task.attention = {
            "question": f"Restatement for {task.id} failed ({event}): {reason}. "
                        "Fix the target session, then re-dispatch."
        }
        task.settled_at = now_ts()
        notice = {
            "event": "async_settled",
            "task": task.id,
            "role": task.role,
            "status": task.status,
            "state": task.state,
            "summary": f"[tower] {task.id} settled: {task.status} | {event}: {reason}",
        }
        board.notify(notice)
        task.notified = _deliver_lead_push(cfg, board, task, notice["summary"])
        board.save(task)
    board.event(event, task=task.id, reason=reason[:300])
    board.log(event, task=task.id, reason=reason[:300])
    return {"outcome": "failed", "error": f"{event}: {reason}"[:300]}


def _settle_restate(cfg: TowerConfig, board: Board, job: dict) -> dict[str, Any]:
    from .dispatch import closed_task, dispatch_restate

    task = board.load(job["task"])
    if not task.mode:
        task.mode = "async"
        board.save(task)
    if closed_task(board, task.id) is not None:
        return _settle_ignored(cfg, board, job, task)
    problem = _restate_target_problem(cfg, task)
    if problem:
        return _restate_failed(cfg, board, task, "restate_target_unavailable", problem)

    budget = _restate_budget(cfg, task)
    muted = _MutedBoard(board)
    outcome: dict[str, Any] = {}

    def run() -> None:
        try:
            outcome["task"] = dispatch_restate(cfg, muted, task)
        except Exception as exc:  # the deadline must report, not die with the thread
            outcome["error"] = f"{type(exc).__name__}: {exc}"

    runner = threading.Thread(target=run, name=f"restate-{task.id}", daemon=True)
    with tower_lock(cfg):
        runner.start()
        runner.join(budget)
        timed_out = runner.is_alive()
        if timed_out:
            muted.mute()
    if timed_out:
        return _restate_failed(cfg, board, task, "restate_timeout",
                               f"no answer within {budget:.0f}s")
    if "error" in outcome:
        raise RuntimeError(outcome["error"])

    task = outcome["task"]
    with tower_lock(cfg):
        task = board.load(task.id)
        where = ("restating — wait for `tower restate ok`"
                 if task.status == "restating" else f"gate result: {task.status}")
        task.settled_at = now_ts()
        notice = {
            "event": "async_settled",
            "task": task.id,
            "role": task.role,
            "status": task.status,
            "state": task.state,
            "summary": f"[tower] {task.id} settled: {task.status} | {where}",
        }
        board.notify(notice)
        task.notified = _deliver_lead_push(cfg, board, task, notice["summary"])
        board.save(task)
    board.event("settled", task=task.id, status=task.status, kind="restate")
    board.log("async_done", task=task.id, kind="restate")
    return {"outcome": task.status}


def _watch_relay(cfg: TowerConfig, board: Board, job: dict) -> dict[str, Any]:
    """Watch for a receipt: an explicit inbox reply, or a reply file (`--wait`)."""
    payload = job.get("payload") or {}
    relay_id = str(payload.get("relay_id", ""))
    target = str(payload.get("to", ""))
    timeout_s = float(payload.get("timeout_s", 300))
    wait_file = str(payload.get("wait_file", ""))
    deadline = time.time() + max(5.0, timeout_s)

    def _finish(outcome: str, sender: str = "", summary: str = "") -> dict[str, Any]:
        with tower_lock(cfg):
            board.notify({
                "event": f"relay_{outcome}",
                "relay": relay_id,
                "from": sender,
                "to": target,
                "summary": summary,
            })
        board.event("notified", relay=relay_id, outcome=outcome)
        board.log(f"relay_{outcome}", relay=relay_id, frm=sender)
        return {"outcome": outcome}

    while time.time() < deadline:
        if wait_file:
            path = Path(wait_file)
            if path.exists():
                try:
                    content = path.read_text(encoding="utf-8")[:400]
                except OSError:
                    content = "(unreadable receipt file)"
                return _finish("receipt", "file", content or "(empty receipt file)")
        else:
            # only an explicit reply (--reply <id>) counts; the tower's own outgoing
            # delivery record also carries the relay id and must not match
            for entry in board.inbox():
                if entry.get("reply") == relay_id:
                    summary = ((entry.get("report") or {}).get("summary")
                               or entry.get("message") or "")[:400]
                    return _finish("receipt", str(entry.get("from", "")), summary)
        time.sleep(POLL_S)
    return _finish("timeout", "", f"no receipt for relay {relay_id} within {int(timeout_s)}s")


def reviews_dir(cfg: TowerConfig) -> Path:
    path = cfg.state_dir / "reviews"
    path.mkdir(parents=True, exist_ok=True)
    return path


def update_reputation(cfg: TowerConfig, reviewer: str, proved: int, dropped: int,
                      out_of_scope: int) -> dict[str, Any]:
    """Per-reviewer trust ledger: `.tower/reviews/<role>.json` (design P2)."""
    path = reviews_dir(cfg) / f"{reviewer}.json"
    try:
        rep = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rep = {"reviewer": reviewer, "reviewed": 0, "proved": 0,
               "dropped": 0, "out_of_scope": 0, "score": 1.0}
    rep["reviewed"] = int(rep.get("reviewed", 0)) + 1
    rep["proved"] = int(rep.get("proved", 0)) + proved
    rep["dropped"] = int(rep.get("dropped", 0)) + dropped
    rep["out_of_scope"] = int(rep.get("out_of_scope", 0)) + out_of_scope
    total = rep["proved"] + rep["dropped"] + rep["out_of_scope"]
    rep["score"] = round(rep["proved"] / total, 2) if total else 1.0
    path.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    return rep


def reflect_skill_draft(cfg: TowerConfig, board: Board, task: Task) -> str | None:
    """P3: same-cause reworks ≥2 → draft a skill from what went wrong.

    Drafts live under `skills/drafts/` and are NOT in the teach index until a
    person adopts them (`tower skill save` still owns the registered path).
    """
    reworks = [h.get("note", "") for h in (task.history or [])
               if h.get("status") == "rework" and h.get("note")]
    if len(reworks) < 2:
        return None
    slug = f"{task.id.lower()}-lesson"
    role_dir = cfg.role_dir(task.role)
    drafts = role_dir / "skills" / "drafts"
    drafts.mkdir(parents=True, exist_ok=True)
    body = (
        f"# Lesson from {task.id} (draft — adopt with `tower skill save` close to this)\n\n"
        f"## When to use\n"
        f"Tasks for `{task.role}` whose brief touches: {task.brief[:120]}\n\n"
        f"## What went wrong (from the rework rounds)\n"
        + "".join(f"- {note[:200]}\n" for note in reworks)
        + "\n## Do instead\n"
        "- Re-read the deliverable against the report contract BEFORE filing.\n"
        "- Run the checks and paste the real output.\n"
    )
    path = drafts / f"{slug}.md"
    if path.exists():
        return None                    # already distilled once — no duplicate notice
    path.write_text(body, encoding="utf-8")
    board.event("lesson_drafted", task=task.id, role=task.role, path=str(path))
    board.notify({"event": "lesson_drafted", "task": task.id, "role": task.role,
                  "summary": f"a repeated failure pattern was drafted into a skill: {path}"})
    return str(path)


def run_one_job(cfg: TowerConfig) -> dict[str, Any] | None:
    """Claim and process one job synchronously (used by tests and `--once`)."""
    pair = _claim_next(cfg)
    if pair is None:
        return None
    claimed_path, job = pair
    _process(cfg, claimed_path, job)
    done = cfg.queue_dir / "done" / claimed_path.name
    try:
        return json.loads(done.read_text(encoding="utf-8")).get("result")
    except (OSError, ValueError):
        return None


def _claimed_job_tasks(cfg: TowerConfig) -> list[str]:
    """Task ids currently owned by a live queue claim (someone is working them)."""
    claimed_dir = cfg.queue_dir / "claimed"
    out = []
    for path in sorted(claimed_dir.glob("*.json")):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if job.get("task"):
            out.append(str(job["task"]))
    return out


def _adopt_orphans(cfg: TowerConfig) -> int:
    """B3: tasks left claimed/in_progress by a dead worker go back to rework.

    The 2026-11-22 report: the worker was stopped mid-run and three tasks stayed
    `running` forever — no surface ever said the owning process was gone. At
    worker start, any claimed/in_progress task that is old (staleness_minutes)
    and NOT covered by a live queue claim is interrupted: rework + audit event.
    """
    from .board import STATUS_REWORK, age_seconds, attention_status

    board = Board(cfg)
    live = set(_claimed_job_tasks(cfg))
    adopted = 0
    for task in board.all():
        if task.status not in ("claimed", "in_progress"):
            continue
        if attention_status(task) in ("claimed", "resolved"):
            continue  # the issues bot owns it
        age = age_seconds(task.updated_at)
        if age is None or age < getattr(cfg, "staleness_minutes", 30) * 60:
            continue
        if task.id in live:
            continue  # a live queue claim owns it
        board.transition(task, STATUS_REWORK,
                         note="interrupted: the worker died mid-run; re-dispatched")
        task.round += 1
        board.save(task)
        board.event("task_interrupted", task=task.id, role=task.role,
                    last_status=task.status)
        adopted += 1
    if adopted:
        board.log("orphans_adopted", count=adopted)
    return adopted


_PROGRESS_TICK_S = 60.0
_last_progress_tick = 0.0
_LATE_TICK_S = 60.0
_last_late_tick = 0.0


def _tick_progress(cfg: TowerConfig) -> None:
    """Ask long-running tasks for a heartbeat line, at most once a minute."""
    global _last_progress_tick
    now = time.time()
    if now - _last_progress_tick < _PROGRESS_TICK_S:
        return
    _last_progress_tick = now
    from .dispatch import progress_window_notice

    progress_window_notice(cfg, Board(cfg))


def _tick_late(cfg: TowerConfig) -> None:
    """Settle `late` tasks whose report landed (or whose horizon is past)."""
    global _last_late_tick
    now = time.time()
    if now - _last_late_tick < _LATE_TICK_S:
        return
    _last_late_tick = now
    from .dispatch import recheck_late

    recheck_late(cfg, Board(cfg))


def run_worker(cfg: TowerConfig, once: bool = False, force: bool = False,
               concurrency: int = 0) -> int:
    """The worker loop with a thread pool (`workers: N`, design doc §1).

    Each thread claims jobs via the atomic `os.replace`; per-role serialisation is
    enforced with `serial_requests` by a semaphore map so the same role never runs
    two adapter turns at once, while different roles go in parallel. A worker
    whose workspace has been deleted (test tmp dirs!) exits instead of polling
    forever — B2 field report, 2026-11-09.

    B3 (2026-11-22): on Ctrl-C the worker **drains** — it refuses to leave
    claimed jobs half-dead. It prints the in-flight task ids and waits for them
    to finish; a second Ctrl-C (or `--force`) exits immediately with a warning.
    """
    global _last_progress_tick, _last_late_tick, _POOL
    from . import __version__

    # One consumer per workspace: a second `tower worker` used to idle silently
    # beside the first, and the operator read that silence as an idle pool.
    handle = _take_worker_lock(cfg)
    if handle is None:
        holder = _lock_holder(cfg)
        print(f"worker: another worker (pid {holder.get('pid', '?')}, started "
              f"{holder.get('started', 'unknown')}) holds .tower/worker.lock — "
              "jobs are already being consumed")
        return 1

    root_existed = cfg.workspace.exists()
    _dirs(cfg)
    _requeue_stale(cfg)
    _adopt_orphans(cfg)
    # A fresh loop asks for heartbeats on its first idle poll — a global left
    # over from an earlier run in the same process would silently skip it.
    _last_progress_tick = 0.0
    _last_late_tick = 0.0
    pool = max(1, int(concurrency or getattr(cfg, "workers", 4) or 1))
    _POOL = pool
    base_url = str((cfg.adapter_options.get("dsh") or {}).get("base_url") or "")
    print(f"worker: pool={pool} pid={os.getpid()} version={__version__} "
          f"base_url={base_url} workspace={cfg.workspace}", flush=True)
    role_locks: dict[str, threading.Semaphore] = {}
    locks_guard = threading.Lock()

    # B4 (2026-11-23): a `kill`/service-stop sends SIGTERM, and a worker that dies
    # on the spot leaves its in-flight turn half-run — the DSH session then accepts
    # messages but never starts a turn (`session/cancel` does not fix that). The
    # handler asks the loops to stop claiming and lets the turn finish; a second
    # signal abandons it the same way a second Ctrl-C does.
    stop = threading.Event()
    previous_handlers: dict[int, Any] = {}

    def _request_stop(signum, _frame) -> None:
        if stop.is_set():
            raise KeyboardInterrupt
        stop.set()
        name = getattr(signal.Signals(signum), "name", str(signum))
        print(f"worker: {name} — draining; send it again to abandon the turn",
              file=sys.stderr)

    for sig_name in ("SIGTERM", "SIGBREAK"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            previous_handlers[sig] = signal.signal(sig, _request_stop)
        except (ValueError, OSError):
            pass  # not the main thread (tests, embedded use) — Ctrl-C still drains

    def role_sem(name: str) -> threading.Semaphore:
        with locks_guard:
            if name not in role_locks:
                role_locks[name] = threading.Semaphore(1)
            return role_locks[name]

    def loop() -> None:
        while True:
            if stop.is_set():
                return  # B4: SIGTERM asked us to stop claiming new work
            if root_existed and not cfg.workspace.exists():
                return  # the workspace was deleted under us (tests/tmp cleanup)
            _heartbeat(cfg)
            pair = _claim_next(cfg)
            if pair is None:
                if once:
                    return
                _tick_progress(cfg)
                _tick_late(cfg)
                time.sleep(POLL_S)
                continue
            claimed_path, job = pair
            job.setdefault("started_ts", time.time())
            job["role"] = _job_role(cfg, job)
            _heartbeat(cfg, job)
            try:
                if cfg.serial_requests and job["role"]:
                    with role_sem(job["role"]):
                        _process(cfg, claimed_path, job)
                else:
                    _process(cfg, claimed_path, job)
            finally:
                _clear_heartbeat(cfg, str(job.get("id") or ""))

    def _drain_or_die() -> None:
        """B3: on Ctrl-C, wait for in-flight jobs instead of abandoning them."""
        inflight = _claimed_job_tasks(cfg)
        if not inflight or force:
            if inflight:
                print(f"worker: --force exit with {len(inflight)} job(s) in flight "
                      f"({', '.join(inflight)}) — they will be adopted on next start",
                      file=sys.stderr)
            raise KeyboardInterrupt
        print(f"worker: stopping, but {len(inflight)} job(s) still in flight "
              f"({', '.join(inflight)}) — draining; Ctrl-C again to abandon",
              file=sys.stderr)
        deadline = time.time() + 3600
        while _claimed_job_tasks(cfg) and time.time() < deadline:
            _heartbeat(cfg)   # stay visible as the consumer while draining
            time.sleep(1)
        raise KeyboardInterrupt

    def _drain_quietly() -> None:
        """B4: finish what was already claimed, then leave without a trace."""
        inflight = _claimed_job_tasks(cfg)
        if not inflight:
            return
        print(f"worker: draining {len(inflight)} in-flight job(s) "
              f"({', '.join(inflight)})", file=sys.stderr)
        deadline = time.time() + 3600
        while _claimed_job_tasks(cfg) and time.time() < deadline:
            _heartbeat(cfg)   # stay visible as the consumer while draining
            time.sleep(1)

    try:
        if pool <= 1:
            try:
                loop()
            except KeyboardInterrupt:
                pass
        else:
            threads = [threading.Thread(target=loop, daemon=True) for _ in range(pool)]
            for thread in threads:
                thread.start()
            try:
                while any(thread.is_alive() for thread in threads):
                    _heartbeat(cfg)
                    if once and queue_depth(cfg) == 0 and not any(thread.is_alive() for thread in threads):
                        break
                    time.sleep(0.2)
            except KeyboardInterrupt:
                _drain_or_die()
        if stop.is_set() and not force:
            _drain_quietly()
    finally:
        for sig, handler in previous_handlers.items():
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError, TypeError):
                pass
        release_lock(handle)
    return 0


def _settle_failure(board: Board, job: dict, outcome: str, error: str) -> dict[str, Any]:
    """A job that never ran: fail its task so the board says what the ledger said.

    The task file can be gone, or it can still be sitting there `pending` while
    the worker only wrote a `missing-task` line — aimigao-zh saw the latter for
    a `--group` that did not exist, and read it as silence.
    """
    result: dict[str, Any] = {"outcome": outcome, "error": error[:300]}
    try:
        task = board.load(job.get("task", ""))
    except (FileNotFoundError, OSError):
        return result
    board.transition(task, STATUS_FAILED, note=f"settlement failed: {error}"[:500])
    result["failed_task"] = task.id
    board.notify({"event": "async_failed", "task": task.id,
                  "summary": f"job {job.get('id')} failed: {error}"[:300]})
    board.log("async_failed", job=job.get("id"), error=error[:300], failed_task=task.id)
    return result


def _job_span(cfg: TowerConfig, job: dict, outcome: str) -> None:
    """One line per finished job — the interval that proves two ran at once."""
    started = float(job.get("started_ts") or 0) or time.time()
    entry = {"job": job.get("id"), "kind": job.get("kind"), "task": job.get("task"),
             "role": str(job.get("role") or ""), "started_ts": started,
             "ended_ts": time.time(), "outcome": outcome}
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    with (cfg.state_dir / "jobs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _process(cfg: TowerConfig, claimed_path: Path, job: dict) -> None:
    # a detached worker can outlive a tower.toml edit: re-read the roles/config
    # for every job, so "unknown role 'auditor'" never happens again
    cfg = reload_config(cfg)
    board = Board(cfg)
    try:
        if job.get("kind") == "dispatch":
            result = _settle_dispatch(cfg, board, job)
        elif job.get("kind") == "adversarial":
            result = _run_adversarial(cfg, board, job)
        elif job.get("kind") == "restate":
            result = _settle_restate(cfg, board, job)
        elif job.get("kind") == "relay_watch":
            result = _watch_relay(cfg, board, job)
        else:
            result = {"outcome": "unknown-kind"}
    except FileNotFoundError as exc:
        result = _settle_failure(board, job, "missing-task", str(exc))
    except ValueError as exc:
        result = _settle_failure(board, job, "refused", str(exc))
    except Exception as exc:  # a worker must never die on one bad job
        result = {"outcome": "error", "error": str(exc)[:300]}
        board.notify({"event": "async_failed", "task": job.get("task", ""),
                      "summary": f"job {job.get('id')} failed: {exc}"[:300]})
        board.log("async_failed", job=job.get("id"), error=str(exc)[:300])
    job["settled_at"] = now_ts()
    job["result"] = result
    _job_span(cfg, job, str(result.get("outcome") or ""))
    (cfg.queue_dir / "done" / claimed_path.name).write_text(
        json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    claimed_path.unlink(missing_ok=True)
