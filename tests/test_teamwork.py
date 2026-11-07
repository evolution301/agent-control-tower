"""Bot-to-bot conversation, huddles and deadline supervision (v0.8, offline).

The mock adapter answers each prompt shape deterministically, and failure paths
use stub adapters so the fails-closed and no-whispering rules are tested, not
assumed.
"""

import json
import sys
from datetime import datetime, timedelta

import pytest

from control_tower import cli
from control_tower.adapters import AdapterResult
from control_tower.board import Board, STATUS_REPORTED
from control_tower.config import ensure_layout, parse_config
from control_tower.converse import (
    load_conversation,
    parse_messages,
    relay_report_messages,
    say,
)
from control_tower.huddle import huddle, parse_decision, parse_position
from control_tower.supervise import (
    due_human,
    is_overdue,
    overdue_tasks,
    parse_due,
    supervise,
)


@pytest.fixture()
def tower(tmp_path, monkeypatch):
    config = {
        "name": "test-tower",
        "workspace": str(tmp_path),
        "roles": {
            "backend": {"description": "server side", "adapter": "mock",
                        "write_scopes": ["roles/backend/"], "max_rounds": 2},
            "frontend": {"description": "browser UI", "adapter": "mock",
                         "write_scopes": ["roles/frontend/"], "max_rounds": 2},
            "qa": {"description": "verification", "adapter": "mock", "max_rounds": 1},
            "research": {"description": "research", "adapter": "mock", "max_rounds": 1},
            "writer": {"description": "writing", "adapter": "mock", "max_rounds": 1},
            "seo": {"description": "seo", "adapter": "mock", "max_rounds": 1},
            "pm": {"description": "seventh role", "adapter": "mock", "max_rounds": 1},
        },
        "verify_commands": [],
    }
    path = tmp_path / "tower.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    cfg = parse_config(path)
    ensure_layout(cfg)
    monkeypatch.chdir(tmp_path)
    return cfg


class _StubAdapter:
    name = "stub"

    def __init__(self, text="", exit_code=0, deliver_exit_code=None):
        self.text = text
        self.exit_code = exit_code
        self.deliver_exit_code = exit_code if deliver_exit_code is None else deliver_exit_code

    def run(self, req):
        return AdapterResult(text=self.text, exit_code=self.exit_code, meta={})

    def deliver(self, req):
        return AdapterResult(text=self.text or "queued", exit_code=self.deliver_exit_code, meta={})


def _use_stub(monkeypatch, text="", exit_code=0, deliver_exit_code=None):
    stub = _StubAdapter(text=text, exit_code=exit_code, deliver_exit_code=deliver_exit_code)
    monkeypatch.setattr("control_tower.converse.get_adapter", lambda *a, **k: stub)
    return stub


# ------------------------------------------------------------- bot-to-bot say


def test_say_is_two_parallel_sends_and_never_blocks_on_a_reply(tower):
    """The archive (the lead's copy) exists before delivery runs; no reply is awaited."""
    board = Board(tower)
    entry = say(tower, board, "backend", "frontend", "which keys go into the shared config?")
    assert entry["conversation"] == "C-0001"
    assert entry["delivered"] is True

    record = load_conversation(tower, "C-0001")
    message = record["messages"][0]
    assert message["from"] == "backend" and message["to"] == "frontend"
    assert message["delivered"] is True
    events = [json.loads(l)["event"] for l in tower.ledger_path.read_text(encoding="utf-8").splitlines()]
    assert "peer_message" in events
    # fire-and-forget: a peer note writes no work artifact and awaits no report
    assert not list((tower.workspace / "roles" / "frontend").glob("*-MOCK-ARTIFACT.md"))


def test_say_refuses_unknown_roles_and_self_messages(tower):
    board = Board(tower)
    with pytest.raises(KeyError):
        say(tower, board, "backend", "ghost", "hi")
    with pytest.raises(ValueError):
        say(tower, board, "backend", "backend", "talking to myself")


def test_say_threads_messages_under_one_conversation(tower):
    board = Board(tower)
    first = say(tower, board, "backend", "frontend", "first question")
    second = say(tower, board, "frontend", "backend", "and my answer back", conversation_id=first["conversation"])
    record = load_conversation(tower, first["conversation"])
    assert len(record["messages"]) == 2
    assert second["conversation"] == first["conversation"]


def test_a_failed_delivery_is_recorded_on_the_message(tower, monkeypatch):
    """An unreachable peer must not fail the sender: the record carries the bad news."""
    _use_stub(monkeypatch, deliver_exit_code=2)
    board = Board(tower)
    entry = say(tower, board, "backend", "qa", "anyone there?")
    assert entry["delivered"] is False
    record = load_conversation(tower, entry["conversation"])
    message = record["messages"][0]
    assert message["delivered"] is False
    assert message["detail"], "the failure detail is on the record"
    events = [json.loads(l) for l in tower.ledger_path.read_text(encoding="utf-8").splitlines()]
    pm = [e for e in events if e["event"] == "peer_message"][-1]
    assert pm["delivered"] is False


# ------------------------------------------------- report-carried relaying


def test_report_messages_are_parsed_by_heading():
    report = (
        "## Status\nready\n\n## Summary\ndone\n\n## Files\n- a.md — x\n\n## Checks\n- `t` → ok\n\n"
        "## Message to frontend\nplease align the header spacing with mine\n\n"
        "## Message to research\nsummarise the serp for this cluster\n"
    )
    out = parse_messages(report)
    assert [m["to"] for m in out] == ["frontend", "research"]
    assert out[0]["message"] == "please align the header spacing with mine"


def test_relay_delivers_report_messages_and_skips_bad_targets(tower):
    board = Board(tower)
    task = board.create("backend", "work with a handoff")
    task.report = {
        "state": "ready", "summary": "s", "files": [], "checks": [],
        "raw": "## Message to frontend\nalign with me\n\n## Message to backend\nnote to self\n\n## Message to ghost\nnobody home\n",
    }
    delivered = relay_report_messages(tower, board, task)
    assert [d["to"] for d in delivered] == ["frontend"]
    events = [json.loads(l) for l in tower.ledger_path.read_text(encoding="utf-8").splitlines()]
    rejected = {e["to"] for e in events if e["event"] == "peer_message_rejected"}
    assert rejected == {"backend", "ghost"}


# ------------------------------------------------------------------- huddles


def test_huddle_two_rounds_six_roles_and_a_minutes_file(tower):
    board = Board(tower)
    roles = ["backend", "frontend", "qa", "research", "writer", "seo"]
    record = huddle(tower, board, roles, "how do we split the landing page")
    assert record["rounds"] == 2
    assert len(record["turns"]) == 12
    r1 = [t for t in record["turns"] if t["round"] == 1]
    assert all(t["position"] for t in r1)
    r2 = [t for t in record["turns"] if t["round"] == 2]
    assert all(t["decision"] for t in r2)
    assert record["path"] and "H-0001.md" in record["path"]
    minutes = (tower.huddles_dir / "H-0001.md").read_text(encoding="utf-8")
    assert "## Round 1" in minutes and "## Round 2" in minutes


def test_huddle_enforces_the_room_limits(tower):
    board = Board(tower)
    with pytest.raises(ValueError, match="at most 6"):
        huddle(tower, board, ["backend", "frontend", "qa", "research", "writer", "seo", "pm"], "x")
    with pytest.raises(ValueError, match="at least two"):
        huddle(tower, board, ["backend"], "x")
    with pytest.raises(ValueError, match="topic"):
        huddle(tower, board, ["backend", "qa"], "  ")
    with pytest.raises(KeyError):
        huddle(tower, board, ["backend", "ghost"], "x")


def test_huddle_rounds_one_stops_after_positions(tower):
    board = Board(tower)
    record = huddle(tower, board, ["backend", "qa"], "quick sync", rounds=1)
    assert record["rounds"] == 1
    assert len(record["turns"]) == 2


def test_huddle_parsers_read_both_rounds():
    position = parse_position("## Position\nI back option A.\n\n## Questions\n- who owns the api\n")
    assert position["position"] == "I back option A."
    assert position["questions"] == ["who owns the api"]
    decision = parse_decision("## Decision\nOption A.\n\n## Commitments\n- ship the schema\n- none\n")
    assert decision["decision"] == "Option A."
    assert decision["commitments"] == ["ship the schema"]


# ---------------------------------------------------------------- supervision


def test_parse_due_reads_relative_and_absolute():
    now = datetime(2026, 11, 8, 10, 0, 0)
    assert parse_due("90m", now) == "2026-11-08T11:30:00"
    assert parse_due("2h", now) == "2026-11-08T12:00:00"
    assert parse_due("3d", now) == "2026-11-11T10:00:00"
    assert parse_due("2026-11-09 18:00") == "2026-11-09T18:00:00"
    assert parse_due("") == ""
    with pytest.raises(ValueError):
        parse_due("next tuesday", now)


def test_due_helpers_report_the_truth():
    now = datetime(2026, 11, 8, 12, 0, 0)
    board_task = Board.__new__(Board)  # only the dataclass fields are used
    from control_tower.board import Task

    task = Task(id="T-0001", role="backend", brief="x", due_at="2026-11-08T11:00:00")
    assert is_overdue(task, now)
    assert due_human(task, now) == "overdue 1h"
    task.due_at = "2026-11-08T13:00:00"
    assert not is_overdue(task, now)
    assert due_human(task, now) == "due in 1h"
    task.due_at = ""
    assert not is_overdue(task, now) and due_human(task, now) == ""


def test_dispatch_sets_a_deadline_and_status_shows_it(tower, capsys):
    cli.main(["dispatch", "backend", "with a deadline", "--due", "2h"])
    task = Board(tower).load("T-0001")
    assert task.due_at, "dispatch --due must stamp the task"
    assert not is_overdue(task)
    capsys.readouterr()
    cli.main(["status"])
    out = capsys.readouterr().out
    assert "due in" in out


def test_supervise_recovers_a_late_report(tower):
    board = Board(tower)
    task = board.create("backend", "late but done")
    task.due_at = "2026-11-08T10:00:00"
    task.status = "in_progress"
    board.save(task)
    now = datetime(2026, 11, 8, 12, 0, 0)
    result = supervise(tower, board, now=now)
    assert result["touched"] == 1
    assert result["results"][0]["outcome"] == "report_recovered"
    reloaded = board.load(task.id)
    assert reloaded.status == STATUS_REPORTED
    assert reloaded.report and reloaded.report["checks"]
    assert reloaded.nudges == 1
    assert reloaded.overdue is False


def test_supervise_records_still_open_and_unreachable(tower, monkeypatch):
    board = Board(tower)
    open_task = board.create("backend", "not finished")
    open_task.due_at = "2026-11-08T10:00:00"
    open_task.status = "in_progress"
    board.save(open_task)
    silent = board.create("frontend", "no answer at all")
    silent.due_at = "2026-11-08T10:00:00"
    silent.status = "in_progress"
    board.save(silent)
    now = datetime(2026, 11, 8, 12, 0, 0)

    # first nudge: a status-only answer (no report) -> still open
    monkeypatch.setattr(
        "control_tower.supervise.get_adapter", lambda *a, **k: _StubAdapter(
            text="## Status\nprogress\n\n## Summary\nhalfway.\n\n## Files\n- none\n\n## Checks\n- none\n"
        )
    )
    result = supervise(tower, board, now=now)
    by_task = {r["task"]: r for r in result["results"]}
    assert by_task[open_task.id]["outcome"] == "still_open"
    assert board.load(open_task.id).overdue is True

    # second nudge: adapter unreachable -> both overdue tasks are chased again
    monkeypatch.setattr(
        "control_tower.supervise.get_adapter", lambda *a, **k: _StubAdapter(text="", exit_code=1)
    )
    result = supervise(tower, board, now=now)
    by_task = {r["task"]: r for r in result["results"]}
    assert by_task[silent.id]["outcome"] == "unreachable"
    assert by_task[open_task.id]["outcome"] == "unreachable"
    assert board.load(silent.id).overdue is True
    assert board.load(silent.id).nudges == 2, "every pass chases every overdue task"
    assert board.load(open_task.id).nudges == 2


def test_supervise_leaves_fresh_tasks_alone(tower):
    board = Board(tower)
    task = board.create("backend", "no deadline yet")
    task.status = "in_progress"
    board.save(task)
    result = supervise(tower, board, now=datetime(2026, 11, 8, 12, 0, 0))
    assert result["touched"] == 0


def test_overdue_tasks_ignores_accepted_and_closed_work(tower):
    board = Board(tower)
    done = board.create("backend", "accepted despite the clock")
    done.due_at = "2026-11-08T10:00:00"
    done.status = "accepted"
    board.save(done)
    assert overdue_tasks(board, tower, now=datetime(2026, 11, 8, 12, 0, 0)) == []


# ----------------------------------------------------------------- CLI wiring


def test_cli_say_and_huddle_and_supervise(tower, capsys):
    assert cli.main(["say", "backend", "frontend", "hello over there"]) == 0
    out = capsys.readouterr().out
    assert "conversation C-0001" in out

    assert cli.main(["huddle", "backend", "qa", "--topic", "split the work"]) == 0
    out = capsys.readouterr().out
    assert "huddle H-0001" in out

    assert cli.main(["supervise"]) == 0
    out = capsys.readouterr().out
    assert "nothing overdue" in out


def test_cli_say_json_is_one_document(tower, capsys):
    assert cli.main(["say", "backend", "qa", "ping", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["conversation"] == "C-0001"


def test_cli_huddle_rejects_seven_roles(tower, capsys):
    rc = cli.main(["huddle", "backend", "frontend", "qa", "research", "writer", "seo", "pm",
                   "--topic", "too many"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "at most 6" in (captured.out + captured.err)
