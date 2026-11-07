"""BI view: rates, lead time, throughput and per-role numbers (offline)."""

import json
import sys
from datetime import datetime, timedelta

import pytest

from control_tower import cli
from control_tower.analytics import (
    build_report,
    lead_time_s,
    percentile,
    render_text,
)
from control_tower.board import STATUS_ACCEPTED, STATUS_REWORK, Board
from control_tower.config import ensure_layout, parse_config
from control_tower.converse import say
from control_tower.dispatch import dispatch
from control_tower.huddle import huddle
from control_tower.skills import save_skill


@pytest.fixture()
def tower(tmp_path, monkeypatch):
    config = {
        "name": "bi-tower",
        "workspace": str(tmp_path),
        "roles": {
            "backend": {"description": "server", "adapter": "mock",
                        "write_scopes": ["roles/backend/"], "max_rounds": 2},
            "qa": {"description": "verify", "adapter": "mock", "max_rounds": 1},
        },
        "verify_commands": [],
    }
    path = tmp_path / "tower.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    cfg = parse_config(path)
    ensure_layout(cfg)
    monkeypatch.chdir(tmp_path)
    return cfg


def _run_and_accept(tower, role="backend", brief="do the thing"):
    board = Board(tower)
    task = board.create(role, brief)
    task = dispatch(tower, board, task)
    board.transition(task, STATUS_ACCEPTED, note="verified")
    return board.load(task.id)


def test_empty_tower_reports_no_rates_instead_of_zero(tower):
    report = build_report(tower, Board(tower))
    assert report["totals"]["tasks"] == 0
    assert report["rates"]["verified"] is None, "no verdicts must never read as 0%"
    assert report["lead_time"]["p50_s"] is None
    text = render_text(report)
    assert "verified rate: —" in text


def test_verified_rate_counts_accepted_over_decided(tower):
    _run_and_accept(tower)
    board = Board(tower)
    reworked = board.create("backend", "came back")
    reworked.status = STATUS_REWORK
    board.save(reworked)

    report = build_report(tower, board)
    assert report["totals"]["accepted"] == 1
    assert report["totals"]["rework"] == 1
    assert report["rates"]["verified"] == pytest.approx(0.5)


def test_lead_time_is_creation_to_acceptance(tower):
    task = _run_and_accept(tower)
    value = lead_time_s(task)
    assert value is not None and value >= 0
    accepted_ts = [h["ts"] for h in task.history if h["status"] == STATUS_ACCEPTED][0]
    assert isinstance(accepted_ts, str)


def test_percentile_is_nearest_rank():
    assert percentile([], 50) is None
    assert percentile([10], 90) == 10
    assert percentile([1, 2, 3, 4, 10], 50) == 3
    assert percentile([1, 2, 3, 4, 10], 90) == 10


def test_throughput_window_and_per_role_numbers(tower):
    _run_and_accept(tower, "backend")
    _run_and_accept(tower, "qa")
    report = build_report(tower, Board(tower), days=7)
    assert len(report["throughput"]) == 7
    today = datetime.now().date().isoformat()
    day = [d for d in report["throughput"] if d["date"] == today][0]
    assert day["created"] == 2 and day["accepted"] == 2
    assert report["roles"]["backend"]["accepted"] == 1
    assert report["roles"]["qa"]["accepted"] == 1
    assert report["engine"]["assignments"] == 2
    assert report["engine"]["by_adapter"]["mock"] == 2


def test_supervision_and_collaboration_are_counted(tower):
    board = Board(tower)
    late = board.create("backend", "missed the deadline")
    late.due_at = "2020-01-01T00:00:00"
    late.overdue = True
    late.nudges = 3
    board.save(late)
    say(tower, board, "backend", "qa", "which keys are shared?")
    huddle(tower, board, ["backend", "qa"], "how do we split this")

    report = build_report(tower, board)
    assert report["supervision"]["overdue_now"] == 1
    assert report["supervision"]["nudges"] == 3
    assert report["collaboration"]["conversations"] == 1
    assert report["collaboration"]["messages"] == 1
    assert report["collaboration"]["huddles"] == 1
    assert report["collaboration"]["turns"] == 4  # two roles × two rounds


def test_skills_are_counted_per_role(tower):
    task = _run_and_accept(tower)
    save_skill(tower, Board(tower), task.id)
    report = build_report(tower, Board(tower))
    assert report["roles"]["backend"]["skills"] == 1
    assert report["roles"]["qa"]["skills"] == 0


def test_cli_stats_json_is_one_document(tower, capsys):
    _run_and_accept(tower)
    assert cli.main(["stats", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["totals"]["accepted"] == 1
    assert "throughput" in data and len(data["throughput"]) == 14


def test_cli_stats_text_has_the_sections(tower, capsys):
    _run_and_accept(tower)
    assert cli.main(["stats"]) == 0
    out = capsys.readouterr().out
    assert "verified rate" in out
    assert "lead time" in out
    assert "throughput" in out
    assert "backend" in out


def test_panel_stats_endpoint_serves_json(tower):
    """The BI section was silent for a release once; this keeps the route honest."""
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    from control_tower.panel import make_handler

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tower))
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/stats", timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
        assert "rates" in data and "throughput" in data
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as response:
            page = response.read().decode("utf-8")
        assert "/api/stats" in page and "Business" in page
    finally:
        httpd.shutdown()
        httpd.server_close()
