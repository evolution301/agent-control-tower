"""Regression pins for the 2026-11-23 live-tower defect list.

The evergocasa tower lead reported five production failures in one morning:

1. `session/updateQueue` answered `{"ok": false, ...}` and the adapter turned
   that into `None`, so a purge script read "no error" and reported "cleared"
   while 181 messages stayed queued;
2. bots talking to bots filled that queue (no direction rule, no quota);
3. every task injected the full 60-line contract again (19 copies in a session);
4. settlement notices for one task stacked up as separate inbox lines;
5. `tower_inbox all=true` returned the whole inbox (74KB) at once.

Each test below fails on the pre-fix code.
"""

from __future__ import annotations

import json

import pytest

from control_tower import peer_policy, queue
from control_tower.adapters.base import AdapterResult
from control_tower.adapters.dsh import DshAdapter, DshRpcError
from control_tower.board import Board, Task
from control_tower.cli import build_parser
from control_tower.config import parse_config
from control_tower.converse import say
from control_tower.dispatch import CONTRACT_COMPACT, contract_plan, record_contract, render_prompt


def _write_toml(tmp_path, extra: list[str] | None = None):
    workspace = tmp_path / "tower"
    workspace.mkdir(exist_ok=True)
    toml = [
        "name: postmortem",
        f"workspace: {workspace.as_posix()}",
        "roles:",
        "  lead:",
        '    description: "lead"',
        "    adapter: mock",
        "  ops:",
        '    description: "ops"',
        "    adapter: mock",
        "  backend:",
        '    description: "backend"',
        "    adapter: mock",
        "adapters:",
        "  dsh:",
        '    base_url: "http://127.0.0.1:1"',
    ]
    toml += extra or []
    path = tmp_path / "tower.toml"
    path.write_text("\n".join(toml) + "\n", encoding="utf-8")
    return path


class _Resp:
    """Minimal urlopen response for the adapter's transport seam."""

    def __init__(self, body: dict):
        self._body = json.dumps(body).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# 1 -------------------------------------------------------------- the lie
def test_rpc_raises_on_ok_false(monkeypatch):
    adapter = DshAdapter(base_url="http://127.0.0.1:1")
    rejected = {
        "result": {
            "ok": False,
            "error": {
                "code": "gateway/arguments-invalid",
                "message": 'typert gateway: missing "request"; unexpected "sessionId"',
            },
        }
    }
    monkeypatch.setattr(
        "control_tower.adapters.dsh.urllib.request.urlopen", lambda *a, **k: _Resp(rejected)
    )
    with pytest.raises(DshRpcError) as info:
        adapter.rpc("session/updateQueue", {"sessionId": "s", "itemId": "i",
                                            "action": {"kind": "remove"}})
    assert info.value.code == "gateway/arguments-invalid"
    assert "missing" in str(info.value)


def test_rpc_wraps_a_flat_argument_dict(monkeypatch):
    seen: dict = {}

    def fake_urlopen(request, timeout=90):
        seen.update(json.loads(request.data.decode("utf-8")))
        return _Resp({"result": {"ok": True, "value": {"accepted": True}}})

    monkeypatch.setattr("control_tower.adapters.dsh.urllib.request.urlopen", fake_urlopen)
    adapter = DshAdapter(base_url="http://127.0.0.1:1")
    value = adapter._result_value(
        adapter.rpc("session/updateQueue", {"sessionId": "s1", "itemId": "m1",
                                            "action": {"kind": "remove"}})
    )
    assert seen["payload"]["args"] == {
        "request": {"sessionId": "s1", "itemId": "m1", "action": {"kind": "remove"}}
    }
    assert value == {"accepted": True}


def test_result_value_still_raises_for_ok_false():
    with pytest.raises(DshRpcError):
        DshAdapter._result_value({"result": {"ok": False, "error": {"code": "x"}}})
    assert DshAdapter._result_value({"result": {"ok": True, "value": 7}}) == 7


# 2 -------------------------------------------------------------- the queue
class FakeQueueAdapter:
    name = "fake"

    def __init__(self, items):
        self.items = list(items)

    def queue_items(self, session_id):
        return list(self.items)

    def update_queue(self, session_id, item_id, action="remove", content=None):
        assert action == "remove"
        self.items = [item for item in self.items if item["id"] != item_id]
        return {"accepted": True}


def _items():
    return queue.items_from_state(
        {
            "queue": [
                {"id": "m-peer", "placement": "queued",
                 "content": [{"type": "text", "text": "## Peer note from ops (not a work brief)\n\nhi"}]},
                {"id": "m-notice", "placement": "queued",
                 "content": [{"type": "text", "text": "[tower] T-1 settled: accepted"}]},
                {"id": "m-task", "placement": "queued",
                 "content": [{"type": "text", "text": "## Contract (read this first)\n\nwork"}]},
            ]
        }
    )


def test_purge_reports_before_and_after():
    adapter = FakeQueueAdapter(_items())
    info = queue.purge(adapter, "session-1", only="peer")
    assert info["queue_before"] == 3
    assert info["queue_after"] == 2
    assert info["removed"] == ["m-peer"]
    assert [item["id"] for item in adapter.items] == ["m-notice", "m-task"]


def test_purge_surfaces_a_refusal_instead_of_zero_removed():
    class Refusing(FakeQueueAdapter):
        def update_queue(self, session_id, item_id, action="remove", content=None):
            raise DshRpcError("session/updateQueue", "gateway/arguments-invalid", "missing request")

    info = queue.purge(Refusing(_items()), "session-1")
    assert info["removed"] == []
    assert info["queue_before"] == 3
    assert info["errors"] and "missing request" in info["errors"][0]


def test_prune_drops_old_notices_but_never_work():
    state = {"queue": []}
    for index in range(5):
        state["queue"].append(
            {"id": f"m-peer-{index}", "placement": "queued",
             "content": [{"type": "text", "text": f"## Peer note from ops (not a work brief) {index}"}]}
        )
    state["queue"].append(
        {"id": "m-task", "placement": "queued",
         "content": [{"type": "text", "text": "## Contract (read this first)\n\nreal work"}]}
    )
    adapter = FakeQueueAdapter(queue.items_from_state(state))
    info = queue.prune(adapter, "session-1", limit=2)
    assert info["queue_before"] == 6
    assert info["pruned"] == 4
    assert info["queue_after"] == 2
    assert [item["id"] for item in adapter.items] == ["m-peer-4", "m-task"]


# 3 -------------------------------------------------------------- peer policy
def test_peer_notes_quota_and_lead_exemption(tmp_path):
    cfg = parse_config(_write_toml(tmp_path, ["peer_notes:", "  mode: quota",
                                              "  lead: lead", "  per_hour: 2"]))
    assert cfg.peer_note_mode == "quota" and cfg.peer_notes_per_hour == 2
    assert peer_policy.check(cfg, "ops", "lead")["allow"] is True
    peer_policy.record(cfg, "ops")
    peer_policy.record(cfg, "ops")
    verdict = peer_policy.check(cfg, "ops", "backend")
    assert verdict["allow"] is False
    assert "quota" in verdict["reason"]
    # the lead stays reachable at any volume
    assert peer_policy.check(cfg, "ops", "lead")["allow"] is True


def test_peer_notes_lead_only_and_open_modes(tmp_path):
    cfg = parse_config(_write_toml(tmp_path, ["peer_notes:", "  mode: lead-only"]))
    assert peer_policy.check(cfg, "ops", "backend")["allow"] is False
    assert peer_policy.check(cfg, "ops", "lead")["allow"] is True
    cfg.peer_note_mode = "open"
    assert peer_policy.check(cfg, "ops", "backend")["allow"] is True


def test_say_files_a_throttled_note_without_delivering(monkeypatch, tmp_path):
    cfg = parse_config(_write_toml(tmp_path, ["peer_notes:", "  per_hour: 1"]))
    delivered: list[str] = []

    class FakeAdapter:
        name = "fake"

        def deliver(self, request):
            delivered.append(request.prompt)
            return AdapterResult(text="queued", exit_code=0)

    monkeypatch.setattr("control_tower.converse.get_adapter", lambda name, opts: FakeAdapter())
    board = Board(cfg)
    first = say(cfg, board, "ops", "backend", "one")
    second = say(cfg, board, "ops", "backend", "two")
    assert first["delivered"] is True
    assert second["delivered"] is False and second["throttled"] is True
    assert sum(1 for prompt in delivered if prompt.startswith("## Peer note")) == 1
    # the breaker notice goes to the sender, once
    assert any("peer note not delivered" in prompt for prompt in delivered)


# 4 -------------------------------------------------------------- contract once
def test_contract_is_compact_after_the_first_dispatch(tmp_path):
    cfg = parse_config(_write_toml(tmp_path, ["contract:", "  repeat_every: 2"]))
    task = Task(id="T-0001", role="ops", brief="do the thing", write_scopes=["out/"])
    first = contract_plan(cfg, task)
    assert first["compact"] is False and "first dispatch" in first["reason"]
    record_contract(cfg, task, first)
    second = contract_plan(cfg, Task(id="T-0002", role="ops", brief="next", write_scopes=["out/"]))
    assert second["compact"] is True
    record_contract(cfg, task, second)
    third = contract_plan(cfg, Task(id="T-0003", role="ops", brief="next", write_scopes=["out/"]))
    assert third["compact"] is True
    record_contract(cfg, task, third)
    fourth = contract_plan(cfg, Task(id="T-0004", role="ops", brief="next", write_scopes=["out/"]))
    assert fourth["compact"] is False
    assert "every 2" in fourth["reason"]


def test_contract_repeats_when_scopes_change(tmp_path):
    cfg = parse_config(_write_toml(tmp_path))
    task = Task(id="T-0001", role="ops", brief="x", write_scopes=["out/"])
    plan = contract_plan(cfg, task)
    record_contract(cfg, task, plan)
    changed = contract_plan(cfg, Task(id="T-0002", role="ops", brief="x", write_scopes=["other/"]))
    assert changed["compact"] is False and "changed" in changed["reason"]


def test_compact_prompt_keeps_task_and_report_file(tmp_path):
    cfg = parse_config(_write_toml(tmp_path))
    task = Task(id="T-0042", role="ops", brief="ship it", write_scopes=["out/"])
    full = render_prompt(cfg, task)
    compact = render_prompt(cfg, task, compact=True)
    assert len(compact) < len(full)
    assert "ship it" in compact and "T-0042-REPORT.md" in compact
    assert compact.startswith(CONTRACT_COMPACT.splitlines()[0])


# 5 -------------------------------------------------------------- inbox noise
def test_settlement_notices_coalesce_per_task(tmp_path):
    cfg = parse_config(_write_toml(tmp_path))
    board = Board(cfg)
    for status in ("reported", "accepted", "accepted"):
        board.notify({"event": "async_settled", "task": "T-0001", "role": "ops",
                      "status": status, "summary": f"[tower] T-0001 settled: {status}"})
    board.notify({"event": "async_settled", "task": "T-0002", "role": "ops", "status": "accepted"})
    entries = board.inbox()
    assert [entry["task"] for entry in entries] == ["T-0001", "T-0002"]
    assert entries[0]["count"] == 3
    assert entries[0]["status"] == "accepted"
    assert entries[0]["first_ts"] <= entries[0]["ts"]


# 6 -------------------------------------------------------------- wiring
def test_queue_subcommand_parses():
    args = build_parser().parse_args(["queue", "purge", "ops", "--only", "peer", "--max", "2"])
    assert args.queue_action == "purge" and args.role == "ops"
    assert args.only == "peer" and args.max == 2
    listing = build_parser().parse_args(["queue", "list", "lead", "--json"])
    assert listing.queue_action == "list" and listing.json is True


def test_inbox_has_pagination_flags():
    args = build_parser().parse_args(["inbox", "--limit", "5", "--offset", "10"])
    assert args.limit == 5 and args.offset == 10
