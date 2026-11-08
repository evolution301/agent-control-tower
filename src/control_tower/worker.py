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
import threading
import time
from pathlib import Path
from typing import Any

from .board import Board, STATUS_ACCEPTED, now_ts
from .config import TowerConfig
from .locks import tower_lock

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
    """Write one job and return it. Jobs are ordered by a monotonic counter.

    Concurrency-safe: the whole pick-id → write → replace section runs under the
    in-process claim lock, and a cross-process `os.replace` collision retries with
    a bumped id instead of clobbering an existing job.
    """
    with _CLAIM_LOCK:
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
    """The one-line lead notice, in the design doc's format (§5)."""
    report = task.report or {}
    summary = str(report.get("summary") or "").replace("\n", " ")[:200]
    return {
        "event": "async_settled",
        "task": task.id,
        "role": task.role,
        "status": task.status,
        "state": task.state,
        "triage": result.get("outcome", ""),
        "summary": f"[tower] {task.id} settled: {task.status}"
                   + (f" (triage: {result.get('outcome')})" if result.get("outcome") else "")
                   + f" | role={task.role} | summary={summary}",
    }


def _deliver_lead_push(cfg: TowerConfig, board: Board, task, text: str) -> bool:
    """`notify.kind = lead_session` → push one message to the lead's DSH session.

    Uses the settled task's own adapter (so mock towers work in tests and dsh
    towers in production). Failures never break the settlement: the inbox entry
    is the durable record, the session push is best-effort.
    """
    if cfg.notify_kind != "lead_session" or not cfg.notify_session:
        return False
    try:
        from .adapters import get_adapter

        role = cfg.roles.get(task.role)
        adapter_name = role.adapter if role else "dsh"
        options = dict(cfg.adapter_options.get(adapter_name) or {})
        adapter = get_adapter(adapter_name, options)
        result = adapter.deliver_to_session(cfg.notify_session, text)
        return result.exit_code == 0
    except Exception:
        return False


def _settle_dispatch(cfg: TowerConfig, board: Board, job: dict) -> dict[str, Any]:
    from .converse import relay_report_messages
    from .dispatch import dispatch
    from .triage import redispatch_with_reasons, triage_task

    task = board.load(job["task"])
    if not task.mode:
        task.mode = "async"
        board.save(task)
    with tower_lock(cfg):
        dispatch(cfg, board, task)
        relay_report_messages(cfg, board, task)
        task = board.load(task.id)
        result = triage_task(
            cfg, board, task,
            dispatch_fn=lambda b, t, reasons: redispatch_with_reasons(cfg, b, t, reasons),
        )
        task = board.load(task.id)
        task.settled_at = now_ts()
        notice = _settlement_notice(cfg, task, result)
        board.notify(notice)
        task.notified = _deliver_lead_push(cfg, board, task, notice["summary"])
        board.save(task)
    board.event("settled", task=task.id, status=task.status,
                triage=result.get("outcome", ""))
    board.event("notified", task=task.id, inbox=True, lead_session=task.notified)
    board.log("async_done", task=task.id, outcome=result.get("outcome", ""))
    return result


def _run_adversarial(cfg: TowerConfig, board: Board, job: dict) -> dict[str, Any]:
    """One independent "break it" pass over a ready task (pstack thermos/interrogate).

    The reviewer's findings only count when they carry a run's proof (command →
    output marker, same bar as Checks). Proved findings rework the origin task
    with the evidence attached; zero proved findings accept it.
    """
    from .converse import relay_report_messages
    from .dispatch import dispatch
    from .triage import redispatch_with_reasons, triage_task
    from .verify import verify_report

    origin_id = job["task"]
    reviewer = str((job.get("payload") or {}).get("reviewer", ""))
    origin = board.load(origin_id)

    review_task = board.create(
        reviewer,
        f"[adversarial review of {origin_id}] Break this change.\n\n"
        f"Scope: ONLY what the report and files below added or changed.\n"
        f"Each finding must carry a run's proof — a command plus the actual output "
        f"that shows wrong behavior — written as one line:\n"
        f"- <what is wrong> | command: <cmd> → output: <output the intent requires>\n"
        f"Leave out anything you did not run. Style is out of scope.\n"
        f"No findings is a valid result. Never misreport severity.\n\n"
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
    triage_task(cfg, board, review_task)          # the reviewer's own report settles
    review_task = board.load(review_task.id)

    raw = review_task.report or {}
    findings = [str(f).strip() for f in (raw.get("findings") or []) if str(f).strip()]
    proved = [f for f in findings if ("→" in f or "->" in f or "`" in f)]
    rounds = int((origin.review or {}).get("rounds", 0)) + 1

    with tower_lock(cfg):
        origin = board.load(origin_id)
        verdict = "pass"
        if proved:
            verdict = "rework"
            redispatch_with_reasons(cfg, board, origin, "; ".join(proved)[:400])
        elif not verify_report(cfg, origin).ok:
            verdict = "rework"
            redispatch_with_reasons(cfg, board, origin, "review pass: verification regressed")
        else:
            board.transition(origin, STATUS_ACCEPTED,
                             note=f"adversarial review passed ({review_task.id})",
                             verification=verify_report(cfg, origin).as_dict())
        origin.review = {"rounds": rounds, "verdict": verdict, "reviewer": reviewer,
                         "findings": len(findings), "proved": len(proved),
                         "review_task": review_task.id}
        notice = {
            "event": "review_verdict",
            "task": origin_id,
            "reviewer": reviewer,
            "verdict": verdict,
            "summary": (f"[tower] {origin_id} review verdict: {verdict} | role={origin.role} "
                        f"| findings={len(findings)} proved={len(proved)} "
                        f"| reviewer={reviewer} ({review_task.id})"),
        }
        board.notify(notice)
        origin.notified = _deliver_lead_push(cfg, board, origin, notice["summary"])
        origin.settled_at = now_ts()
        board.save(origin)
    board.event("adversarial_verdict", origin=origin_id, review=review_task.id,
                verdict=verdict, findings=len(findings), proved=len(proved))
    board.log("adversarial_done", origin=origin_id, review=review_task.id,
              verdict=verdict, proved=len(proved))
    return {"outcome": verdict, "findings": len(findings), "proved": len(proved)}


def _settle_restate(cfg: TowerConfig, board: Board, job: dict) -> dict[str, Any]:
    from .dispatch import dispatch_restate

    task = board.load(job["task"])
    if not task.mode:
        task.mode = "async"
        board.save(task)
    with tower_lock(cfg):
        dispatch_restate(cfg, board, task)
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


def run_worker(cfg: TowerConfig, once: bool = False) -> int:
    """The worker loop with a thread pool (`workers: N`, design doc §1).

    Each thread claims jobs via the atomic `os.replace`; per-role serialisation is
    enforced with `serial_requests` by a semaphore map so the same role never runs
    two adapter turns at once, while different roles go in parallel.
    """
    _dirs(cfg)
    _requeue_stale(cfg)
    pool = max(1, int(getattr(cfg, "workers", 4) or 1))
    role_locks: dict[str, threading.Semaphore] = {}
    locks_guard = threading.Lock()

    def role_sem(name: str) -> threading.Semaphore:
        with locks_guard:
            if name not in role_locks:
                role_locks[name] = threading.Semaphore(1)
            return role_locks[name]

    def loop() -> None:
        while True:
            _heartbeat(cfg)
            pair = _claim_next(cfg)
            if pair is None:
                if once:
                    return
                time.sleep(POLL_S)
                continue
            claimed_path, job = pair
            role = str((job.get("payload") or {}).get("role") or "")
            if cfg.serial_requests and role:
                with role_sem(role):
                    _process(cfg, claimed_path, job)
            else:
                _process(cfg, claimed_path, job)

    if pool <= 1:
        loop()
        return 0
    threads = [threading.Thread(target=loop, daemon=True) for _ in range(pool)]
    for thread in threads:
        thread.start()
    while any(thread.is_alive() for thread in threads):
        _heartbeat(cfg)
        if once and queue_depth(cfg) == 0 and not any(thread.is_alive() for thread in threads):
            break
        time.sleep(0.2)
    return 0


def _process(cfg: TowerConfig, claimed_path: Path, job: dict) -> None:
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
