"""The panel must stay current by itself, and one port must mean one panel."""

import json
import socket
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from control_tower import panel
from control_tower.config import ensure_layout, parse_config
from control_tower.panel import PanelServer, should_restart, source_fingerprint


@pytest.fixture()
def tower(tmp_path):
    config = {"name": "hot-test", "workspace": str(tmp_path),
              "roles": {"backend": {"description": "s", "adapter": "mock"}}}
    path = tmp_path / "tower.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    cfg = parse_config(path)
    ensure_layout(cfg)
    return cfg


def test_fingerprint_is_stable_and_tracks_changes(tmp_path):
    for name in panel.SOURCE_FILES:
        (tmp_path / name).write_text("x", encoding="utf-8")
    first = source_fingerprint(tmp_path)
    assert first == source_fingerprint(tmp_path)
    (tmp_path / "panel.py").write_text("changed", encoding="utf-8")
    assert source_fingerprint(tmp_path) != first


def test_should_restart_only_when_the_code_moved():
    assert should_restart("aaa", "bbb")
    assert not should_restart("aaa", "aaa")
    assert not should_restart("", "bbb"), "no baseline means nothing to compare"


def test_a_second_panel_refuses_the_same_port():
    """Two panels on one port was a real bug: requests hit whichever won the race."""
    first = PanelServer(("127.0.0.1", 0), None) if False else None  # keep import used
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.listen(1)
    try:
        with pytest.raises(OSError):
            PanelServer(("127.0.0.1", port), None)
    finally:
        sock.close()
        assert first is None


def test_health_reports_version_and_instance(tower):
    from control_tower import __version__
    from control_tower.panel import make_handler

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tower))
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
        assert data["ok"] is True
        assert data["version"] == __version__
        assert data["instance"] and len(data["instance"]) >= 6
        assert data["pid"] > 0
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_page_polls_the_instance_and_reloads(tower):
    from control_tower.panel import render_page

    page = render_page(tower, "zh")
    assert "/api/health" in page
    assert "location.reload()" in page
