"""Huddle discussion turns must complete without a REPORT file (T-0071).

The defect: a discussion turn was waited on with the task-report rule
(`report.exists()`), so every role burned `wait_s` and the meeting came back
empty — and a stale rolling `-REPORT.md` from an earlier task made the adapter
return old text instantly. These tests pin both exits: a discussion turn
returns when the session's turn counter advances, and a report is only
accepted when it was written after delivery.
"""

import json
import threading
import time
from pathlib import Path

from control_tower import huddle as huddle_mod
from control_tower.adapters.base import AdapterRequest, AdapterResult
from control_tower.adapters.dsh import DshAdapter, DshRpcError
from control_tower.board import Board
from control_tower.config import ensure_layout, parse_config
from control_tower.huddle import huddle


def _tower(tmp_path, roles, timeout_s=2):
    workspace = tmp_path / "tower"
    workspace.mkdir(exist_ok=True)
    path = tmp_path / "tower.json"
    path.write_text(json.dumps({
        "name": "huddle-discuss",
        "workspace": str(workspace),
        "roles": {name: {"description": name, "adapter": "mock"} for name in roles},
        "huddle": {"timeout_s": timeout_s},
    }), encoding="utf-8")
    cfg = parse_config(path)
    ensure_layout(cfg)
    return cfg


def _fake_turn(monkeypatch, behaviour):
    """behaviour: role -> "answer" | "timeout" | "raise"."""
    seen: list[dict] = []

    class FakeAdapter:
        def run(self, req):
            seen.append({"role": req.role, "options": dict(req.options or {})})
            act = behaviour.get(req.role, "answer")
            if act == "raise":
                raise RuntimeError("session went away")
            if act == "timeout":
                time.sleep(30)
            text = (
                "## Position\nShip the smallest fix.\n\n"
                "## Questions\n- who owns the rollback?\n"
            )
            return AdapterResult(exit_code=0, text=text)

    monkeypatch.setattr("control_tower.converse.get_adapter", lambda name, options: FakeAdapter())
    return seen


def test_answered_turns_carry_text_and_no_absent_roles(tmp_path, monkeypatch):
    cfg = _tower(tmp_path, ["cli", "qa"])
    seen = _fake_turn(monkeypatch, {})

    record = huddle(cfg, Board(cfg), ["cli", "qa"], "smoke: one line please")

    assert record["discuss"]["state"] == "done"
    assert record["discuss"]["absent"] == []
    first_round = [t for t in record["turns"] if t["round"] == 1]
    assert all(t["absent"] is False for t in first_round)
    assert all(t["answer"].strip() for t in first_round)
    # discussion turns say so in the request, so the adapter never waits on a report
    assert seen and all(s["options"]["expect_report"] is False for s in seen)


def test_timeout_is_bounded_and_says_why(tmp_path, monkeypatch):
    cfg = _tower(tmp_path, ["cli", "qa"], timeout_s=1)
    _fake_turn(monkeypatch, {"qa": "timeout"})
    started = time.monotonic()

    record = huddle(cfg, Board(cfg), ["cli", "qa"], "smoke: one line please")

    assert time.monotonic() - started < 5
    assert record["discuss"]["state"] == "partial"
    assert record["discuss"]["absent"] == ["qa"]
    absent = [t for t in record["turns"] if t["absent"]]
    assert absent and absent[0]["reason"] == "did not answer within the huddle deadline"


def test_exception_is_reported_not_swallowed(tmp_path, monkeypatch):
    cfg = _tower(tmp_path, ["cli", "qa"])
    _fake_turn(monkeypatch, {"qa": "raise"})

    record = huddle(cfg, Board(cfg), ["cli", "qa"], "smoke: one line please")

    absent = [t for t in record["turns"] if t["absent"]]
    assert absent and absent[0]["role"] == "qa"
    assert "session went away" in absent[0]["reason"]
    assert "qa" in record["discuss"]["absent"]


class _FakeRpc:
    """A gateway whose `turnOutline` is the truth about which turn is ours.

    The host truncates `turnOutline[].prompt` to 50 characters, which is why the
    adapter's marker sits at the front of the prompt and this fake truncates the
    same way.
    """

    PREAMBLE = "Let me check the repository first."

    def __init__(self, turns=3, seq=10, text="OLD-ANSWER", advance_to=None,
                 answer=True, started=True, preamble=False):
        self.turns = turns
        self.seq = seq
        self.text = text
        self.advance_to = advance_to
        self.answer = answer
        self.started = started
        self.preamble = preamble
        self.pages = 0
        self.prompts = 0
        self.delivered = ""
        self.outline = [{"turn": turns, "seq": seq - 6, "prompt": "an earlier prompt",
                         "response": text[:120]}]

    def __call__(self, method, args=None, timeout=90):
        if method == "session/prompt":
            self.prompts += 1
            request = (args or {}).get("request") or {}
            content = request.get("content") or [{}]
            self.delivered = content[0].get("text", "")
            if self.started:
                self.turns = self.advance_to if self.advance_to is not None else self.turns + 1
                self.seq += 7
                if self.answer:
                    self.text = "PONG-NEW"
                self.outline.append({"turn": self.turns, "seq": self.seq - 6,
                                     "prompt": self.delivered[:50],
                                     "response": self.text[:120] if self.answer else ""})
            return {"result": {"ok": True, "value": {}}}
        if method == "session/projections":
            return {"result": {"ok": True, "value": {
                "asOfSeq": self.seq,
                "values": {
                    "sessionStats": {"turns": self.turns, "llmMs": 10, "steps": 1},
                    "turnOutline": self.outline,
                    "modelSelection": {"lastUsed": {"model": "m", "provider": "p"}},
                },
            }}}
        if method == "session/page":
            self.pages += 1
            records = []
            first_step = self.answer and self.preamble and self.pages == 1
            if self.answer:
                text = self.PREAMBLE if first_step else self.text
                records.append({"type": "event", "event": {
                    "type": "assistant/message", "seq": self.seq - 1,
                    "data": {"turn": self.turns, "step": 1, "stream": False,
                             "message": {"role": "assistant",
                                         "content": [{"type": "text", "text": text}]}},
                }})
            if not first_step:
                records.append({"type": "event", "event": {
                    "type": "turn/end", "seq": self.seq,
                    "data": {"turn": self.turns, "reason": {"kind": "completed"}},
                }})
            return {"result": {"ok": True, "value": {"records": records}}}
        raise DshRpcError(method, "gateway/bad-request", "unexpected %s" % method)


def _event(kind, seq, **data):
    return {"type": "event", "event": {"type": kind, "seq": seq,
                                       "data": {"turn": 4, **data}}}


def _adapter(tmp_path, rpc, wait_s=2):
    adapter = DshAdapter(sessions={"qa": "S-1"}, wait_s=wait_s, poll_s=0.05, queue_max=0)
    adapter.rpc = rpc
    return adapter, AdapterRequest(role="qa", prompt="answer in one line",
                                   cwd=tmp_path, task_id="T-1", timeout_s=wait_s)


def test_discussion_turn_returns_when_the_turn_counter_advances(tmp_path):
    rpc = _FakeRpc(advance_to=4)
    adapter, req = _adapter(tmp_path, rpc)
    req.options = {"expect_report": False}
    started = time.monotonic()

    result = adapter.run(req)

    assert time.monotonic() - started < 2
    assert result.exit_code == 0
    assert result.text == "PONG-NEW"
    assert result.meta["mode"] == "turn"
    assert result.meta["turns"] == 4
    assert not list(Path(tmp_path).glob("*-REPORT.md"))


def test_discussion_turn_without_an_answer_exits_with_a_reason(tmp_path):
    adapter, req = _adapter(tmp_path, _FakeRpc(answer=False), wait_s=1)
    req.options = {"expect_report": False}

    result = adapter.run(req)

    assert result.exit_code == 4
    assert result.meta["reason"] == "empty turn"
    assert result.meta["mode"] == "turn"


def test_a_busy_session_is_reported_not_answered_with_someone_elses_turn(tmp_path):
    # Our prompt is still queued (no turn of ours in the outline) while an
    # earlier answer exists: returning it would be "answered, but not to me".
    adapter, req = _adapter(tmp_path, _FakeRpc(started=False, text="SOMEBODY ELSE'S ANSWER"), wait_s=1)
    req.options = {"expect_report": False}

    result = adapter.run(req)

    assert result.exit_code == 4
    assert result.meta["started"] is False
    assert "busy" in result.text


def test_a_stale_report_is_never_returned_as_this_turn(tmp_path):
    rpc = _FakeRpc()
    adapter, req = _adapter(tmp_path, rpc)
    stale = Path(tmp_path) / "T-1-REPORT.md"
    stale.write_text("STALE REPORT FROM THE LAST TASK", encoding="utf-8")

    outcome: dict = {}

    def call():
        outcome["result"] = adapter.run(req)

    worker = threading.Thread(target=call, daemon=True)
    worker.start()
    time.sleep(0.4)
    assert worker.is_alive(), "a pre-existing report must not satisfy the wait"
    assert stale.exists(), "the previous task's report must not be deleted"

    time.sleep(0.02)
    stale.write_text("FRESH REPORT FOR THIS TURN", encoding="utf-8")
    worker.join(3)

    assert outcome["result"].exit_code == 0
    assert outcome["result"].text == "FRESH REPORT FOR THIS TURN"
    assert rpc.prompts == 1


def test_a_wholly_failed_huddle_is_not_packaged_as_done(tmp_path, monkeypatch):
    # qa T-0073 finding 1: round 1 marked the turns absent but forgot the role,
    # so discuss.state stayed "done" and a dead meeting looked like a plan.
    cfg = _tower(tmp_path, ["cli", "qa"])

    def dead(cfg, role, prompt, **kwargs):
        return object(), AdapterResult(text="[dsh] no session", exit_code=4)

    monkeypatch.setattr(huddle_mod, "run_turn", dead)
    record = huddle(cfg, Board(cfg), ["cli", "qa"], "smoke: 一句话回答", rounds=1)

    assert record["discuss"]["state"] != "done"
    assert sorted(record["discuss"]["absent"]) == ["cli", "qa"]
    assert all(turn["absent"] and turn["reason"] for turn in record["turns"])


def test_first_step_preamble_is_not_the_answer(tmp_path):
    # qa T-0073 finding 2: `sessionStats.turns` counts a turn at its first
    # closed STEP, so the preamble used to be returned as the answer.
    adapter, req = _adapter(tmp_path, _FakeRpc(preamble=True), wait_s=5)
    req.options = {"expect_report": False}

    result = adapter.run(req)

    assert result.exit_code == 0
    assert result.text == "PONG-NEW"
    assert req.options["expect_report"] is False


class _NoTurnEndRpc:
    """A gateway that never logs `turn/end`: settling text is the only boundary.

    Step 1 is a preamble with a tool call (open until poll 4), step 2 the real
    answer. Step events are paired so a closed step does not read as busy.
    """

    def __init__(self):
        self.polls = 0
        self.turns = 3
        self.seq = 10
        self.outline = []

    def __call__(self, method, args=None, timeout=90):
        if method == "session/prompt":
            request = (args or {}).get("request") or {}
            text = ((request.get("content") or [{}])[0]).get("text", "")
            self.turns, self.seq = 4, 17
            self.outline = [{"turn": 4, "seq": 11, "prompt": text[:50], "response": ""}]
            return {"result": {"ok": True, "value": {}}}
        if method == "session/projections":
            return {"result": {"ok": True, "value": {"asOfSeq": self.seq, "values": {
                "sessionStats": {"turns": self.turns, "llmMs": 10, "steps": 1},
                "turnOutline": self.outline,
                "modelSelection": {"lastUsed": {"model": "m", "provider": "p"}}}}}}
        if method == "session/page":
            self.polls += 1
            records = [_event("step/start", 11, step=1), {"type": "event", "event": {
                "type": "assistant/message", "seq": 14,
                "data": {"turn": 4, "step": 1, "message": {"role": "assistant",
                         "content": [{"type": "text", "text": "Let me check the repository first."},
                                     {"type": "tool-call", "name": "read"}]}}}}]
            if self.polls >= 4:
                records += [
                    _event("step/end", 16, step=1),
                    _event("step/start", 18, step=2),
                    {"type": "event", "event": {
                        "type": "assistant/message", "seq": 19,
                        "data": {"turn": 4, "step": 2, "message": {"role": "assistant",
                                 "content": [{"type": "text", "text": "FINAL ANSWER"}]}}}},
                    _event("step/end", 20, step=2),
                ]
            return {"result": {"ok": True, "value": {"records": records}}}
        raise DshRpcError(method, "gateway/bad-request", "unexpected %s" % method)


def test_a_gateway_without_turn_end_settles_on_stable_text(tmp_path):
    rpc = _NoTurnEndRpc()
    adapter, req = _adapter(tmp_path, rpc, wait_s=5)
    req.options = {"expect_report": False}

    result = adapter.run(req)

    assert result.exit_code == 0
    assert result.text == "FINAL ANSWER"
    assert rpc.polls >= 4


class _ToolOnlyLastStepRpc:
    """A finished turn whose newest step is tool-only; the answer is one step back."""

    def __init__(self):
        self.polls = 0
        self.marker = ""

    def __call__(self, method, args=None, timeout=90):
        if method == "session/prompt":
            request = (args or {}).get("request") or {}
            text = ((request.get("content") or [{}])[0]).get("text", "")
            self.marker = text[:50]
            return {"result": {"ok": True, "value": {}}}
        if method == "session/projections":
            return {"result": {"ok": True, "value": {
                "asOfSeq": 40,
                "values": {
                    "sessionStats": {"turns": 4, "llmMs": 10, "steps": 2},
                    "turnOutline": [{"turn": 4, "seq": 11, "prompt": self.marker,
                                     "response": "EARLIER ANSWER"[:120]}],
                    "modelSelection": {"lastUsed": {"model": "m", "provider": "p"}}}}}}
        if method == "session/page":
            self.polls += 1
            records = [
                _event("step/start", 12, step=1),
                {"type": "event", "event": {
                    "type": "assistant/message", "seq": 13,
                    "data": {"turn": 4, "step": 1, "message": {"role": "assistant",
                             "content": [{"type": "text", "text": "EARLIER ANSWER"}]}}}},
                _event("step/end", 14, step=1),
                _event("step/start", 15, step=2),
                {"type": "event", "event": {
                    "type": "assistant/message", "seq": 16,
                    "data": {"turn": 4, "step": 2, "message": {"role": "assistant",
                             "content": [{"type": "tool-call", "name": "read"}]}}}},
                _event("step/end", 17, step=2),
                _event("turn/end", 18),
            ]
            return {"result": {"ok": True, "value": {"records": records}}}
        raise DshRpcError(method, "gateway/bad-request", f"unexpected {method}")


def test_an_earlier_step_answer_survives_a_tool_only_last_step(tmp_path):
    rpc = _ToolOnlyLastStepRpc()
    adapter, req = _adapter(tmp_path, rpc, wait_s=2)
    req.options = {"expect_report": False}

    result = adapter.run(req)

    assert result.exit_code == 0, result.text
    assert result.text == "EARLIER ANSWER"
    assert (result.meta or {}).get("signal") == "turn/end"


def test_huddle_collect_uses_the_discussion_path(monkeypatch, tmp_path):
    cfg = _tower(tmp_path, ["cli", "qa"])
    calls: list[dict] = []

    def fake_run_turn(cfg_, role, prompt, task_id="", expect_report=True, timeout_s=None):
        calls.append({"role": role, "expect_report": expect_report, "timeout_s": timeout_s})
        return object(), AdapterResult(exit_code=0, text="## Position\nok")

    monkeypatch.setattr(huddle_mod, "run_turn", fake_run_turn)

    huddle(cfg, Board(cfg), ["cli", "qa"], "smoke")

    assert calls and all(c["expect_report"] is False for c in calls)
    assert all(c["timeout_s"] == cfg.huddle_timeout_s for c in calls)
