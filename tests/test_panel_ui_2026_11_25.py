"""Panel UI: vendored Pico CSS, offline, with the write guard intact."""

import contextlib
import json
import re
import subprocess
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from control_tower.board import Board
from control_tower.config import ensure_layout, parse_config
from control_tower.panel import make_handler


def _tower(root):
    config = {
        "name": "panel-ui",
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


@contextlib.contextmanager
def _served(cfg):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cfg))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()


def _get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as response:
        return response.status, response.read()


def _post(port, path, body, origin):
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Origin": origin},
        method="POST",
    )
    return urllib.request.urlopen(request, timeout=5)


def test_page_uses_the_vendored_stylesheet_and_semantic_markup(tower):
    with _served(tower) as port:
        status, body = _get(port, "/")
    html = body.decode("utf-8")
    assert status == 200
    assert '<link rel="stylesheet" href="/assets/pico.css">' in html
    assert '<meta name="color-scheme" content="light dark">' in html
    assert "<main" in html and "<article" in html and "<table" in html and "<form" in html
    assert 'id="askform"' in html and 'action="/api/ask"' not in html


def test_stylesheet_is_the_vendored_mit_build(tower):
    with _served(tower) as port:
        status, body = _get(port, "/assets/pico.css")
    head = body[:400].decode("utf-8")
    assert status == 200
    assert head.startswith('@charset "UTF-8";/*!')
    assert "Pico CSS" in head and "Licensed under MIT" in head
    assert b"--pico-" in body


def test_page_has_no_cdn_stylesheet_references(tower):
    with _served(tower) as port:
        _status, body = _get(port, "/")
    html = body.decode("utf-8")
    assert re.search(r'<link[^>]+href="https?://', html) is None
    assert "cdn.jsdelivr" not in html and "unpkg.com" not in html


def test_matte_material_parameters_are_exact(tower):
    with _served(tower) as port:
        _status, body = _get(port, "/")
    html = body.decode("utf-8")
    for token in (
        "--page:#FAFAF8", "--section:#F4F3F0", "--card:#FFFFFF", "--line:#D2CDC2",
        "--page:#1F1F26", "--section:#26262F", "--line:#3B3B48", "--radius:14px",
        "--pico-card-box-shadow:none", "box-shadow:none!important",
        "#2F7D4F", "#B3402E", "#2F5FA8", "#B07A1F", "#6B4FA8",
        "feTurbulence", "feColorMatrix", "opacity:.3",
    ):
        assert token in html
    css = html.split("<style>", 1)[1].split("</style>", 1)[0]
    assert "box-shadow:none!important" in css
    assert re.search(r"box-shadow:\s*(?!none)", css) is None
    assert "gradient" not in css
    assert css.count("\n") + 2 <= 60


def test_forged_origin_is_still_refused_and_writes_nothing(tower):
    with _served(tower) as port:
        with pytest.raises(urllib.error.HTTPError) as caught:
            _post(port, "/api/ask", {"text": "do as I say"},
                  f"http://127.0.0.1.evil.com:{port}")
        assert caught.value.code == 403
        try:
            detail = caught.value.read()
        except ConnectionError:
            detail = b""
    assert b"Origin" in detail and b"not this panel" in detail
    assert Board(tower).all() == []


def test_office_lock_files_never_reach_the_project_list(tmp_path, monkeypatch):
    from control_tower import registry

    home = tmp_path / "home"
    monkeypatch.setenv("TOWER_HOME", str(home))
    junk = tmp_path / "~$hqnext.docx"
    stale = tmp_path / "stale.tmp"
    junk.mkdir()
    stale.mkdir()
    real = tmp_path / "real"
    real.mkdir()
    tower = _tower(real)
    registry.save_projects([
        {"name": "~$hqnext.docx", "path": str(junk)},
        {"name": "stale.tmp", "path": str(stale)},
        {"name": "real", "path": str(tower.workspace)},
    ])
    with _served(tower) as port:
        _status, body = _get(port, "/api/projects")
        _status, page = _get(port, "/")
    names = [p["name"] for p in json.loads(body.decode("utf-8"))["projects"]]
    assert "~$hqnext.docx" not in names and "stale.tmp" not in names
    assert "real" in names
    assert "~$hqnext" not in page.decode("utf-8")


def test_no_dangling_next_due_label_when_nothing_is_due(tower):
    with _served(tower) as port:
        _status, body = _get(port, "/")
    html = body.decode("utf-8")
    assert "function nextLine(next) {" in html
    assert 'if (!next || !next.due) return "";' in html
    assert "${nextLine(next)}" in html
    assert "const countdown = next ?" not in html


def test_logo_is_a_small_chip_not_a_giant_placeholder(tower):
    with _served(tower) as port:
        _status, body = _get(port, "/")
    html = body.decode("utf-8")
    assert '<span class="logochip" id="mark" aria-hidden="true">' in html
    assert 'bot("ok", "#3b82f6", 42)' not in html
    assert 'bot(next ? "ok" : "ok"' not in html
    assert 'width="96"' not in html
    css = html.split("<style>", 1)[1].split("</style>", 1)[0]
    assert ".logochip{" in css and "width:28px" in css and "border-radius:8px" in css
    assert ".logochip.big{width:32px" in css
    assert "background:var(--section)" in css and "1px solid var(--line)" in css


def test_controls_use_the_matte_chip_tokens(tower):
    with _served(tower) as port:
        _status, body = _get(port, "/")
    html = body.decode("utf-8")
    css = html.split("<style>", 1)[1].split("</style>", 1)[0]
    assert "#28508D" in css and "#4A4A55" in css
    assert "border-radius:10px" in css
    assert ".seg a,.seg button{" in css
    assert 'data-theme-choice="dark"' in html and ">暗</button>" in html
    assert "linear-gradient" not in css
    assert re.search(r"box-shadow:\s*(?!none)", css) is None


def test_next_due_line_renders_a_human_due_value(tower):
    with _served(tower) as port:
        _status, body = _get(port, "/")
    script = body.decode("utf-8").split("<script>", 1)[1].split("</script>", 1)[0]

    def grab(start, end):
        i = script.index(start)
        return script[i:script.index(end, i)]

    src = grab("function nextLine(", "function drawCoord(") + grab("function parseDue(", "function allTasks(")
    program = ("const esc=(v)=>String(v??'');" + src +
               "process.stdout.write(JSON.stringify([nextLine({due:'due in 58m'}),"
               "nextLine({due:'overdue 2h'}),nextLine({})]));")
    out = subprocess.run(["node", "-e", program], capture_output=True, text=True,
                            encoding="utf-8", timeout=30)
    assert out.returncode == 0, out.stderr
    human, late, empty = json.loads(out.stdout)
    assert "下一次到期" in human and "due in 58m" in human
    assert "下一次到期" in late and "overdue 2h" in late
    assert empty == ""
