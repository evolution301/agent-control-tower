"""Regression tests for queue inspection on stream-only DSH gateways (2026-11-23).

`tower queue list` used to die with a raw gateway string because
`session/observeState` is a stream-carrier RPC: a plain POST answers
`gateway/signature-invalid`. Queue inspection must degrade honestly while
`session/prompt` and `session/updateQueue` keep working.
"""

import pytest

from control_tower import queue
from control_tower.adapters.dsh import DshAdapter, DshRpcError

STREAM_MESSAGE = (
    "typert gateway: session/observeState: stream Remote methods must be opened "
    "through the stream carrier"
)


def _adapter() -> DshAdapter:
    return DshAdapter(base_url="http://127.0.0.1:1", queue_max=5)


def _refuse_stream(monkeypatch, adapter: DshAdapter) -> list[str]:
    calls: list[str] = []

    def rpc(method, args=None, timeout=90):
        calls.append(method)
        raise DshRpcError(method, "gateway/signature-invalid", STREAM_MESSAGE)

    monkeypatch.setattr(adapter, "rpc", rpc)
    return calls


def test_observe_state_records_the_refusal_and_returns_none(monkeypatch):
    adapter = _adapter()
    _refuse_stream(monkeypatch, adapter)

    assert adapter.observe_state("s-1") is None
    assert "signature-invalid" in adapter.observe_error
    assert "stream carrier" in adapter.observe_error


def test_list_items_raises_queue_unavailable(monkeypatch):
    adapter = _adapter()
    _refuse_stream(monkeypatch, adapter)

    with pytest.raises(queue.QueueUnavailable) as caught:
        queue.list_items(adapter, "s-1")
    assert isinstance(caught.value, RuntimeError)
    assert str(caught.value) == queue.UNAVAILABLE
    assert "stream-only" in str(caught.value)


def test_purge_by_id_never_observes_the_queue(monkeypatch):
    adapter = _adapter()
    calls: list[tuple[str, dict]] = []

    def rpc(method, args=None, timeout=90):
        calls.append((method, args or {}))
        return {"result": {"ok": True, "value": {"accepted": True}}}

    monkeypatch.setattr(adapter, "rpc", rpc)

    info = queue.purge(adapter, "s-1", ids=["m1"])

    assert [method for method, _ in calls] == ["session/updateQueue"]
    assert calls[0][1] == {
        "request": {
            "sessionId": "s-1",
            "itemId": "m1",
            "action": {"kind": "remove"},
        }
    }
    assert info["removed"] == ["m1"]
    assert info["errors"] == []
    assert info["queue_before"] == "unknown"
    assert info["queue_after"] == "unknown"
    assert adapter.observe_error == ""


def test_auto_prune_stops_asking_after_the_refusal(monkeypatch):
    adapter = _adapter()
    calls = _refuse_stream(monkeypatch, adapter)
    assert adapter.observe_state("s-1") is None
    assert calls == ["session/observeState"]

    assert adapter._auto_prune("s-1") == {}
    assert calls == ["session/observeState"]  # no second gateway round trip


def test_deliver_still_sends_the_prompt_after_a_refused_observation(monkeypatch):
    from control_tower.adapters.base import AdapterRequest

    adapter = _adapter()
    calls: list[str] = []

    def rpc(method, args=None, timeout=90):
        calls.append(method)
        if method == "session/observeState":
            raise DshRpcError(method, "gateway/signature-invalid", STREAM_MESSAGE)
        return {"result": {"ok": True, "value": {}}}

    monkeypatch.setattr(adapter, "rpc", rpc)
    monkeypatch.setattr(adapter, "resolve_session", lambda req: ("s-1", "pinned"))

    result = adapter.deliver(AdapterRequest(role="cli", prompt="hello", task_id="T-1", cwd="."))

    assert result.exit_code == 0
    assert calls == ["session/observeState", "session/prompt"]
    assert "queued into s-1" in result.text
    assert "signature-invalid" in adapter.observe_error
