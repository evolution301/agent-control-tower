"""Panel write guard: only this page, only the project it was started in."""

import contextlib
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from control_tower import registry
from control_tower.board import Board
from control_tower.config import ensure_layout, parse_config
from control_tower.panel import _loopback, _port_or_default, make_handler

QUESTION = {"question": "Overlay or push content?"}


def _tower(root, name="panel-test"):
    root.mkdir(parents=True, exist_ok=True)
    config = {
        "name": name,
        "workspace": str(root),
        "roles": {
            "backend": {"description": "server", "adapter": "mock",
                        "write_scopes": ["roles/backend/"]},
            "qa": {"description": "verify", "adapter": "mock", "write_scopes": []},
        },
    }
    path = root / "tower.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    cfg = parse_config(path)
    ensure_layout(cfg)
    return cfg


@pytest.fixture()
def tower(tmp_path):
    return _tower(tmp_path)


def _blocked(cfg) -> str:
    board = Board(cfg)
    task = board.create("qa", "Verify the drawer at 390")
    task.status, task.state = "reported", "blocked"
    task.attention = dict(QUESTION)
    board.save(task)
    return task.id


@contextlib.contextmanager
def _served(cfg, allow_projects=False):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0),
                                make_handler(cfg, allow_projects=allow_projects))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _post(port: int, path: str, body: dict, origin: str):
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Origin": origin},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_cross_site_post_is_refused_and_writes_nothing(tower):
    task_id = _blocked(tower)
    with _served(tower) as port:
        code, payload = _post(port, "/api/actions", {"action": "skip", "task": task_id},
                              origin="http://evil.example")
    assert code == 403
    assert "Origin http://evil.example" in payload["error"]
    assert Board(tower).load(task_id).attention == QUESTION


def test_lookalike_loopback_origin_is_refused(tower):
    task_id = _blocked(tower)
    with _served(tower) as port:
        code, payload = _post(port, "/api/actions", {"action": "skip", "task": task_id},
                              origin=f"http://127.0.0.1.evil.com:{port}")
    assert code == 403
    assert "127.0.0.1.evil.com" in payload["error"]
    assert Board(tower).load(task_id).attention == QUESTION


def test_loopback_parser_and_default_ports():
    assert _loopback("127.0.0.1")
    assert _loopback("127.0.0.2")
    assert _loopback("::1")
    assert _loopback("localhost")
    assert not _loopback("127.0.0.1.evil.com")
    assert not _loopback("")
    assert _port_or_default(None, "http") == 80
    assert _port_or_default(None, "https") == 443
    assert _port_or_default(8787, "http") == 8787


def test_same_origin_post_still_works(tower):
    task_id = _blocked(tower)
    with _served(tower) as port:
        code, payload = _post(port, "/api/actions", {"action": "skip", "task": task_id},
                              origin=f"http://127.0.0.1:{port}")
    assert code == 200
    assert payload["ok"] is True and payload["outcome"] == "dismissed"
    assert Board(tower).load(task_id).attention is None


def test_other_projects_need_an_explicit_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("TOWER_HOME", str(tmp_path / "home"))
    started = _tower(tmp_path / "started", name="started")
    other = _tower(tmp_path / "other", name="other")
    registry.add_project(other.workspace, "other")
    task_id = _blocked(other)

    with _served(started) as port:
        code, payload = _post(port, "/api/actions?project=other",
                              {"action": "skip", "task": task_id},
                              origin=f"http://127.0.0.1:{port}")
    assert code == 403
    assert "not enabled for writes" in payload["error"]
    assert Board(other).load(task_id).attention == QUESTION

    own_id = _blocked(started)
    with _served(started) as port:
        code, payload = _post(port, "/api/actions?project=started",
                              {"action": "skip", "task": own_id},
                              origin=f"http://127.0.0.1:{port}")
    assert code == 200 and payload["ok"] is True

    with _served(started, allow_projects=True) as port:
        code, payload = _post(port, "/api/actions?project=other",
                              {"action": "skip", "task": task_id},
                              origin=f"http://127.0.0.1:{port}")
    assert code == 200 and payload["ok"] is True
    assert Board(other).load(task_id).attention is None
