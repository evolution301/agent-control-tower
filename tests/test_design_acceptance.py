"""Design-doc acceptance (plugin-async-relay-design.md): fields, pool, locks, relay."""

import json
import threading
import time

import pytest

from control_tower import worker
from control_tower.board import STATUS_ACCEPTED, Board
from control_tower.config import ensure_layout, parse_config
from control_tower.relay import duration_seconds, send_relay


@pytest.fixture()
def tower(tmp_path, monkeypatch):
    config = {
        "name": "design-tower",
        "workspace": str(tmp_path),
        "roles": {
            "seo": {"description": "s", "adapter": "mock", "max_rounds": 2},
            "ops": {"description": "o", "adapter": "mock"},
            "content": {"description": "c", "adapter": "mock"},
        },
        "workers": 4,
    }
    path = tmp_path / "tower.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    cfg = parse_config(path)
    ensure_layout(cfg)
    monkeypatch.chdir(tmp_path)
    return cfg


def test_async_dispatch_stamps_mode_waiter_and_settlement_fields(tower, capsys, monkeypatch):
    monkeypatch.setattr(worker, "ensure_worker", lambda cfg: True)
    assert cli_dispatch(tower, capsys, "fields probe", "--notify", "session:session-lead") == 0
    board = Board(tower)
    task = board.load("T-0001")
    assert task.mode == "async"
    assert task.waiter == {"kind": "lead_session", "target": "session-lead"}
    assert worker.run_worker(tower, once=True) == 0
    done = board.load("T-0001")
    assert done.settled_at and done.notified is False  # inbox notice; no lead session configured


def cli_dispatch(tower, capsys, brief, *flags):
    from control_tower import cli

    return cli.main(["dispatch", "seo", brief, "--async", "--json", *flags])


def test_notify_lead_session_pushes_one_message(tower, monkeypatch, capsys):
    pushes = []

    class Capture:
        def deliver_to_session(self, session_id, text):
            pushes.append((session_id, text))
            sent = __import__("control_tower.adapters", fromlist=["AdapterResult"])
            return sent.AdapterResult(text="pushed", exit_code=0)

    monkeypatch.setattr("control_tower.adapters.mock.MockAdapter.deliver_to_session",
                        Capture().deliver_to_session)
    tower.notify_kind = "lead_session"
    tower.notify_session = "session-lead"
    assert cli_dispatch(tower, capsys, "push me") == 0
    assert worker.run_worker(tower, once=True) == 0
    board = Board(tower)
    assert board.load("T-0001").notified is True
    assert pushes and pushes[0][0] == "session-lead"
    # the doc's notice shape: "[tower] T-00xx settled: accepted | role=… | summary=…"
    assert "[tower] T-0001 settled:" in pushes[0][1] and "role=seo" in pushes[0][1]


def test_worker_pool_drains_10_concurrent_enqueues(tower):
    """Design §8: 10 concurrent enqueues — no lost or corrupted jobs."""
    board = Board(tower)
    tasks = [board.create("seo", f"stress {i}") for i in range(10)]  # ids assigned serially
    errors: list[Exception] = []

    def enqueue_one(task_id: str) -> None:
        try:
            worker.enqueue(tower, "dispatch", task_id)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=enqueue_one, args=(t.id,)) for t in tasks]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert worker.queue_depth(tower) == 10, "no job lost or clobbered"
    job_ids = [json.loads(p.read_text(encoding="utf-8"))["id"]
               for p in (tower.queue_dir / "pending").glob("*.json")]
    assert len(set(job_ids)) == 10, "ids are unique"
    assert worker.run_worker(tower, once=True) == 0
    assert sum(1 for t in board.all() if t.status == STATUS_ACCEPTED) == 10
    for t in board.all():
        assert t.settled_at


def test_relay_wait_file_is_the_receipt(tower):
    board = Board(tower)
    adapter = __import__("control_tower.adapters", fromlist=["get_adapter"]).get_adapter("mock", {})
    wait_file = tower.workspace / "reply.txt"
    entry = send_relay(tower, board, adapter, to="tower", message="q",
                       await_receipt=True, timeout_s=60, wait_file=str(wait_file))
    assert worker.queue_depth(tower) == 1
    wait_file.write_text("the file receipt", encoding="utf-8")
    assert worker.run_worker(tower, once=True) == 0
    receipts = [e for e in board.inbox() if e.get("event") == "relay_receipt"]
    assert receipts and receipts[0]["summary"] == "the file receipt"


def test_relay_role_target_uses_the_role_session(tower):
    board = Board(tower)
    adapter = __import__("control_tower.adapters", fromlist=["get_adapter"]).get_adapter("mock", {})
    entry = send_relay(tower, board, adapter, to="role:seo", message="note to a role")
    assert entry["delivered"] is True and entry["resolution"] == "role"
    assert "relay_sent" in tower.ledger_path.read_text(encoding="utf-8")


def test_relay_to_unknown_role_is_rejected(tower):
    board = Board(tower)
    adapter = __import__("control_tower.adapters", fromlist=["get_adapter"]).get_adapter("mock", {})
    with pytest.raises(ValueError, match="unknown role"):
        send_relay(tower, board, adapter, to="role:ghost", message="x")


def test_timeout_accepts_the_doc_grammar(tower):
    assert duration_seconds("45m") == 45 * 60
    assert duration_seconds("2h") == 7200
    assert duration_seconds("90s") == 90
    assert duration_seconds("garbage") == 300, "falls back to the default"


def test_events_jsonl_records_the_lifecycle(tower, capsys, monkeypatch):
    monkeypatch.setattr(worker, "ensure_worker", lambda cfg: True)
    assert cli_dispatch(tower, capsys, "audit me") == 0
    assert worker.run_worker(tower, once=True) == 0
    events_path = tower.state_dir / "events.jsonl"
    kinds = [json.loads(line)["event"] for line in events_path.read_text(encoding="utf-8").splitlines()]
    assert "enqueued" in kinds and "settled" in kinds and "notified" in kinds


def test_snapshot_counts_async_in_flight(tower, capsys, monkeypatch):
    from control_tower.snapshot import build_snapshot

    monkeypatch.setattr(worker, "ensure_worker", lambda cfg: True)
    assert cli_dispatch(tower, capsys, "in flight") == 0
    snap = build_snapshot(tower)
    assert snap.counts["async_in_flight"] == 1
