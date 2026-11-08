"""Async dispatch (queue + worker) and the generic relay."""

import json
import time
from pathlib import Path

import pytest

from control_tower import cli, worker
from control_tower.board import STATUS_ACCEPTED, Board
from control_tower.config import ensure_layout, parse_config
from control_tower.relay import next_relay_id, send_relay


@pytest.fixture()
def tower(tmp_path, monkeypatch):
    config = {
        "name": "async-tower",
        "workspace": str(tmp_path),
        "roles": {
            "seo": {"description": "s", "adapter": "mock", "max_rounds": 2},
            "ops": {"description": "o", "adapter": "mock", "dispatch_mode": "async"},
        },
        "dispatch_mode": "sync",
    }
    path = tmp_path / "tower.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    cfg = parse_config(path)
    ensure_layout(cfg)
    monkeypatch.chdir(tmp_path)
    return cfg


def test_dispatch_mode_config_and_role_override(tower):
    assert tower.dispatch_mode == "sync"
    assert tower.effective_dispatch_mode("seo") == "sync"
    assert tower.effective_dispatch_mode("ops") == "async", "the role override wins"
    assert tower.effective_dispatch_mode("nobody") == "sync"


def test_enqueue_writes_an_ordered_job_file(tower):
    first = worker.enqueue(tower, "dispatch", "T-0001")
    second = worker.enqueue(tower, "dispatch", "T-0002")
    assert first["id"] != second["id"]
    pending = tower.queue_dir / "pending"
    assert len(list(pending.glob("*.json"))) == 2
    assert worker.queue_depth(tower) == 2


def test_worker_once_settles_a_dispatch_job_end_to_end(tower):
    board = Board(tower)
    task = board.create("seo", "async work")
    board.save(task)
    worker.enqueue(tower, "dispatch", task.id)

    assert worker.run_worker(tower, once=True) == 0

    done = board.load(task.id)
    assert done.status == STATUS_ACCEPTED, "mock report passes verification"
    notices = [e for e in board.inbox() if e.get("event") == "async_settled"]
    assert notices and notices[0]["task"] == task.id
    assert "async_done" in tower.ledger_path.read_text(encoding="utf-8")
    assert list((tower.queue_dir / "done").glob("*.json")), "the settled job is archived"
    assert worker.queue_depth(tower) == 0


def test_cli_dispatch_async_returns_fast_and_queues(tower, capsys, monkeypatch):
    spawned = []
    monkeypatch.setattr(worker, "ensure_worker", lambda cfg: spawned.append(cfg) or True)

    import time

    started = time.perf_counter()
    assert cli.main(["dispatch", "seo", "async from the cli", "--async", "--json"]) == 0
    elapsed = time.perf_counter() - started

    assert elapsed < 5.0, "queued, not run (CI-safe bound; real budget is <1s)"
    data = json.loads(capsys.readouterr().out.strip())
    assert data["queued"] is True and data["mode"] == "async" and data["id"]
    assert spawned, "the worker is started for the caller"
    board = Board(tower)
    task = board.load(data["id"])
    assert task.status == "pending", "the adapter has not run yet"
    assert worker.queue_depth(tower) == 1

    # the worker settles it afterwards
    assert worker.run_worker(tower, once=True) == 0
    assert board.load(task.id).status == STATUS_ACCEPTED


def test_role_dispatch_mode_async_needs_no_flag(tower, capsys, monkeypatch):
    monkeypatch.setattr(worker, "ensure_worker", lambda cfg: True)
    assert cli.main(["dispatch", "ops", "role default is async", "--json"]) == 0
    data = json.loads(capsys.readouterr().out.strip())
    assert data["queued"] is True
    capsys.readouterr()
    assert cli.main(["dispatch", "seo", "still sync", "--json"]) == 0
    data = json.loads(capsys.readouterr().out.strip())
    assert "queued" not in data


def test_ensure_worker_spawns_detached_only_when_no_heartbeat(tower, monkeypatch):
    calls = []
    monkeypatch.setattr(worker.subprocess, "Popen",
                        lambda argv, **kw: calls.append(argv) or object())
    assert worker.ensure_worker(tower) is True
    assert calls and calls[0][-1] == "worker" and "-m" in calls[0], "spawned: -m control_tower ... worker"
    # a fresh heartbeat means no second spawn
    worker._heartbeat(tower)
    assert worker.ensure_worker(tower) is False
    assert len(calls) == 1


# ---- relay ----------------------------------------------------------------

def test_relay_to_the_tower_alias_lands_in_the_inbox(tower):
    board = Board(tower)
    adapter = __import__("control_tower.adapters", fromlist=["get_adapter"]).get_adapter("mock", {})
    entry = send_relay(tower, board, adapter, to="tower", message="hello from outside")
    assert entry["delivered"] is True
    assert entry["resolution"] == "tower-inbox"
    msgs = [e for e in board.inbox() if e.get("event") == "relay_message"]
    assert msgs and msgs[0]["relay"] == entry["id"]
    assert "relay_sent" in tower.ledger_path.read_text(encoding="utf-8")


def test_relay_receipt_watch_forwarded_to_the_lead(tower):
    board = Board(tower)
    adapter = __import__("control_tower.adapters", fromlist=["get_adapter"]).get_adapter("mock", {})
    entry = send_relay(tower, board, adapter, to="tower", message="question",
                       await_receipt=True, timeout_s=60)
    assert worker.queue_depth(tower) == 1, "the watcher job is queued"

    # the peer answers through the same channel, carrying the relay id
    board.notify({"event": "relay_message", "relay": entry["id"], "reply": entry["id"],
                  "from": "outside-bot",
                  "message": "here is the answer", "report": {"summary": "here is the answer", "self_review": "no findings — checks re-run"}})

    assert worker.run_worker(tower, once=True) == 0
    receipts = [e for e in board.inbox() if e.get("event") == "relay_receipt"]
    assert receipts and receipts[0]["from"] == "outside-bot"
    assert "here is the answer" in receipts[0]["summary"]
    assert "relay_receipt" in tower.ledger_path.read_text(encoding="utf-8")


def test_relay_watch_times_out_and_says_so(tower, monkeypatch):
    board = Board(tower)
    adapter = __import__("control_tower.adapters", fromlist=["get_adapter"]).get_adapter("mock", {})
    entry = send_relay(tower, board, adapter, to="tower", message="nobody home",
                       await_receipt=True, timeout_s=5)
    # shrink the deadline so the test stays fast
    pending = next((tower.queue_dir / "pending").glob("*.json"))
    job = json.loads(pending.read_text(encoding="utf-8"))
    job["payload"]["timeout_s"] = 1
    pending.write_text(json.dumps(job), encoding="utf-8")

    time.sleep(1.1)
    assert worker.run_worker(tower, once=True) == 0
    timeouts = [e for e in board.inbox() if e.get("event") == "relay_timeout"]
    assert timeouts and timeouts[0]["relay"] == entry["id"]


def test_relay_to_an_unknown_title_is_a_clean_error(tower):
    board = Board(tower)
    adapter = __import__("control_tower.adapters", fromlist=["get_adapter"]).get_adapter("mock", {})
    with pytest.raises(ValueError, match="no DSH session"):
        send_relay(tower, board, adapter, to="no-such-session", message="x")


def test_relay_ids_are_ordered(tower):
    a = next_relay_id(tower)
    (tower.state_dir / "relay" / f"{a}.json").write_text("{}", encoding="utf-8")
    b = next_relay_id(tower)
    assert b != a and int(b.split("-")[1]) == int(a.split("-")[1]) + 1
