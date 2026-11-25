"""P0-B: one busy role must not stall the meeting (T-0031, 2026-11-24).

The customer tower watched `kickoff --discuss` print nothing for 600–900 s and
then found minutes.md with only the brief. Every turn is now bounded by
`huddle.timeout_s` (default 90, hard cap 120), and a missing role is recorded
absent instead of holding the room.
"""

import json
import time
from pathlib import Path

from control_tower import kickoff
from control_tower.adapters.base import AdapterResult
from control_tower.board import Board
from control_tower.config import ensure_layout, parse_config
from control_tower.huddle import huddle


def _tower(tmp_path, roles, timeout_s=1):
    workspace = tmp_path / "tower"
    workspace.mkdir(exist_ok=True)
    path = tmp_path / "tower.json"
    path.write_text(json.dumps({
        "name": "huddle-timeout",
        "workspace": str(workspace),
        "roles": {name: {"description": name, "adapter": "mock"} for name in roles},
        "huddle": {"timeout_s": timeout_s},
    }), encoding="utf-8")
    cfg = parse_config(path)
    ensure_layout(cfg)
    return cfg


def _fake_adapter(monkeypatch, slow):
    """A fake adapter: `slow` never replies in time, everyone else answers."""
    class FakeAdapter:
        def run(self, req):
            if req.role in slow:
                time.sleep(5)
            text = (
                "## Position\nShip the smallest fix and keep the diff local.\n\n"
                "## Decision\nShip the smallest fix because it is reversible.\n\n"
                "## Commitments\n- write the failing test (scope: tests/test_fix.py)\n"
            )
            return AdapterResult(exit_code=0, text=text)

    monkeypatch.setattr("control_tower.converse.get_adapter", lambda name, options: FakeAdapter())


def test_all_roles_absent_returns_timeout_fast(tmp_path, monkeypatch):
    cfg = _tower(tmp_path, ["slow-a", "slow-b"])
    _fake_adapter(monkeypatch, {"slow-a", "slow-b"})
    started = time.monotonic()

    record = huddle(cfg, Board(cfg), ["slow-a", "slow-b"], "fix the signup bug")

    assert time.monotonic() - started < 3
    assert record["discuss"]["state"] == "timeout"
    assert record["discuss"]["absent"] == ["slow-a", "slow-b"]
    assert record["discuss"]["seconds"] <= 2
    assert all(turn["absent"] for turn in record["turns"])
    assert record["rounds"] == 1, "no answers means round 2 would only wait again"
    markdown = Path(record["path"]).read_text(encoding="utf-8")
    assert "(absent)" in markdown and "No reply" in markdown

def test_one_absent_role_is_partial_and_the_rest_still_commit(tmp_path, monkeypatch):
    cfg = _tower(tmp_path, ["fast", "slow"])
    _fake_adapter(monkeypatch, {"slow"})

    record = huddle(cfg, Board(cfg), ["fast", "slow"], "fix the signup bug")

    assert record["discuss"]["state"] == "partial"
    assert record["discuss"]["absent"] == ["slow"]
    assert record["discuss"]["seconds"] <= 3
    fast = [t for t in record["turns"] if t["role"] == "fast" and t["round"] == 2][0]
    assert fast["absent"] is False and "smallest fix" in fast["decision"]
    assert record["rounds"] == 2


def test_kickoff_writes_the_package_from_a_partial_answer(tmp_path, monkeypatch):
    cfg = _tower(tmp_path, ["fast", "slow"])
    _fake_adapter(monkeypatch, {"slow"})

    result = kickoff.kickoff(
        cfg, Board(cfg), "fix the signup bug", ["fast", "slow"],
        title="signup", discuss=True, rounds=2,
    )

    assert result["discuss"]["state"] == "partial"
    assert result["discuss"]["absent"] == ["slow"]
    assert result["package"]["plan"]["best"], "a partial meeting still records the answer"
    assert result["package"]["draft"] is True
    assert "absent" in Path(result["minutes"]).read_text(encoding="utf-8")


def test_kickoff_all_absent_stays_draft_and_returns_fast(tmp_path, monkeypatch):
    cfg = _tower(tmp_path, ["slow-a", "slow-b"])
    _fake_adapter(monkeypatch, {"slow-a", "slow-b"})
    started = time.monotonic()

    result = kickoff.kickoff(
        cfg, Board(cfg), "fix the signup bug", ["slow-a", "slow-b"],
        title="signup", discuss=True,
    )

    assert time.monotonic() - started < 4
    assert result["discuss"]["state"] == "timeout"
    assert result["package"]["draft"] is True
    assert result["package"]["plan"]["best"] == ""
    assert result["problems"]
