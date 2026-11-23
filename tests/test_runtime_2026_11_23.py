"""Field defects from a customer tower on 0.18.0 (ed01f2f) — bootstrap.

P0-1a: a legal bare-scalar `base_url:` line was never rewritten, yet the command
still reported the detected value as written.
P0-1b: `detect_base_url` adopted the tower's own panel (:9012), which answers
200 with an empty body, as the harness.
"""

from __future__ import annotations

import contextlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from control_tower import bootstrap, cli
from control_tower.config import parse_config


def _tower_toml(tmp_path, base_url_line):
    workspace = tmp_path / "tower"
    workspace.mkdir(exist_ok=True)
    lines = [
        "name: runtime",
        f"workspace: {workspace.as_posix()}",
        "roles:",
        "  qa:",
        '    description: "qa"',
        "    adapter: mock",
        "adapters:",
        "  dsh:",
    ]
    if base_url_line is not None:
        lines.append("    " + base_url_line)
    path = tmp_path / "tower.toml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "line",
    [
        "base_url: http://127.0.0.1:1",       # bare scalar: the reported defect
        'base_url: "http://127.0.0.1:1"',     # double quoted
        "base_url: 'http://127.0.0.1:1'",     # single quoted
    ],
)
def test_apply_connection_rewrites_every_legal_scalar(tmp_path, line):
    path = _tower_toml(tmp_path, line)
    changed = bootstrap.apply_connection(path, base_url="http://127.0.0.1:54250")
    assert changed == ["base_url"]
    assert parse_config(path).adapter_options["dsh"]["base_url"] == "http://127.0.0.1:54250"


def test_bootstrap_exits_2_when_base_url_could_not_be_written(tmp_path, monkeypatch, capsys):
    """No line to rewrite -> the detected value is NOT reported as written."""
    path = _tower_toml(tmp_path, base_url_line=None)
    monkeypatch.setattr(bootstrap, "detect_secret", lambda workspace: "c2VjcmV0")
    monkeypatch.setattr(bootstrap, "detect_base_url", lambda **kw: ("http://127.0.0.1:54250", False))
    code = cli.main(["--workspace", str(path), "bootstrap"])
    said = capsys.readouterr()
    assert code == 2
    assert "could not write base_url http://127.0.0.1:54250 — edit tower.toml by hand" in (
        said.out + said.err
    )
    assert "base_url:" not in path.read_text(encoding="utf-8")


class _Responder(BaseHTTPRequestHandler):
    status = 200
    body = b""

    def do_POST(self):  # noqa: N802 - http.server API
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        if self.body:
            self.wfile.write(self.body)

    def log_message(self, *args):  # silence
        pass


@contextlib.contextmanager
def _responder(status, body):
    handler = type("_H", (_Responder,), {"status": status, "body": body})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_an_empty_200_is_not_a_harness(tmp_path, monkeypatch):
    """The tower panel answers 200 with an empty body; 0.18.0 adopted it."""
    with _responder(200, b"") as port:
        kind, _ = bootstrap.probe("http://127.0.0.1:%d" % port, "")
        assert kind is None
        monkeypatch.setattr(bootstrap, "listening_ports", lambda: [port])
        assert bootstrap.detect_base_url() == (None, False)


def test_only_a_real_handshake_or_a_401_is_adopted(tmp_path, monkeypatch):
    panel_ok = {"result": {"ok": True, "value": {"items": []}}}
    with _responder(200, b"") as panel, _responder(200, json.dumps(panel_ok).encode()) as good:
        monkeypatch.setattr(bootstrap, "listening_ports", lambda: [panel, good])
        assert bootstrap.detect_base_url() == ("http://127.0.0.1:%d" % good, False)

    with _responder(401, b"") as locked:
        monkeypatch.setattr(bootstrap, "listening_ports", lambda: [locked])
        assert bootstrap.detect_base_url() == ("http://127.0.0.1:%d" % locked, True)
