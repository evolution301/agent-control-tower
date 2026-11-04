"""Tests for the panel: one snapshot, two renderings (terminal + HTTP)."""

import json
import threading
import urllib.request

import pytest

from control_tower.board import Board
from control_tower.config import ensure_layout, parse_config
from control_tower.board_view import render
from control_tower.snapshot import build_snapshot
from control_tower.panel import make_handler
from http.server import ThreadingHTTPServer


@pytest.fixture()
def tower(tmp_path):
    config = {
        "name": "panel-test",
        "workspace": str(tmp_path),
        "roles": {
            "backend": {"description": "server", "adapter": "mock", "write_scopes": ["roles/backend/"]},
            "qa": {"description": "verify", "adapter": "mock", "write_scopes": []},
        },
    }
    path = tmp_path / "tower.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    cfg = parse_config(path)
    ensure_layout(cfg)
    return cfg


def _seed(cfg):
    board = Board(cfg)
    ready = board.create("backend", "Ship the orders endpoint")
    ready.status, ready.state = "reported", "ready"
    board.save(ready)

    blocked = board.create("qa", "Verify the drawer at 390")
    blocked.status, blocked.state = "reported", "blocked"
    blocked.attention = {"question": "Overlay or push content?"}
    board.save(blocked)

    working = board.create("backend", "Backfill the index")
    working.status, working.state = "in_progress", "progress"
    board.save(working)
    return board


def test_snapshot_orders_attention_first_and_tracks_roles(tower):
    _seed(tower)
    snap = build_snapshot(tower)
    assert [a["id"] for a in snap.attention] == ["T-0001", "T-0002"]
    assert snap.counts["attention"] == 2
    assert snap.counts["in_flight"] == 1
    roles = {r["name"]: r for r in snap.roles}
    assert roles["backend"]["doing"] == "working"
    assert roles["backend"]["task_id"] == "T-0003"
    assert roles["qa"]["doing"] == "waiting"


def test_terminal_render_puts_attention_above_the_fold(tower):
    _seed(tower)
    text = render(build_snapshot(tower), colour=False, width=100)
    assert "NEEDS YOU" in text
    assert "T-0002" in text
    assert "Overlay or push content?" in text
    # the attention block must come before the ledger
    assert text.index("NEEDS YOU") < text.index("LEDGER")


def test_panel_serves_html_and_snapshot(tower):
    _seed(tower)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tower))
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as r:
            html = r.read().decode("utf-8")
        assert r.status == 200
        assert "panel-test" in html and "/api/snapshot" in html

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/snapshot", timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
        assert data["counts"]["attention"] == 2
        assert data["attention"][0]["id"] == "T-0001"

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=5) as r:
            assert json.loads(r.read().decode("utf-8"))["ok"] is True
    finally:
        httpd.shutdown()
        httpd.server_close()
