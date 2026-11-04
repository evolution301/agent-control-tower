"""Tests for the DSH adapter wire format and session resolution.

The desktop harness accepts `/api/<method>` with an HMAC-signed cookie and a
`{"type":"client-request",...,"payload":{"args":...}}` envelope; a plain
`POST /rpc/<method>` answers 401. These tests pin that contract, plus the
"no hand-copied session ids" path: reuse a session by title, create one when
absent, and remember it in `.tower/dsh-sessions.json`.
"""

import base64
import hashlib
import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from control_tower.adapters.base import AdapterRequest
from control_tower.adapters.dsh import DshAdapter, auth_cookie, b64d


def test_auth_cookie_is_signed_and_self_describing():
    secret = b64d("aFTT3A_wbecgjFU-vHnMGH4MonyUhbeIDoRywNzaxNY")
    cookie = auth_cookie("127.0.0.1:52713", secret, now_ms=1_700_000_000_000)
    name, value = cookie.split("=", 1)
    expected_name = "dsh-auth-" + base64.urlsafe_b64encode(
        hashlib.sha256(b"127.0.0.1:52713").digest()
    ).rstrip(b"=").decode("ascii")
    assert name == expected_name
    version, body, signature = value.split(".")
    assert version == "v1"
    payload = json.loads(b64d(body))
    assert payload == {
        "version": 1,
        "authority": "127.0.0.1:52713",
        "issuedAt": 1_700_000_000_000,
        "expiresAt": 1_700_000_000_000 + 30 * 24 * 3600 * 1000,
    }
    expected_sig = base64.urlsafe_b64encode(
        hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest()
    ).rstrip(b"=").decode("ascii")
    assert signature == expected_sig


def test_envelope_shape_matches_the_client_request_contract():
    adapter = DshAdapter(base_url="http://127.0.0.1:1")
    envelope = adapter.envelope("session/prompt", {"request": {"sessionId": "session-a"}})
    assert envelope["type"] == "client-request"
    assert envelope["method"] == "session/prompt"
    assert envelope["payload"] == {"args": {"request": {"sessionId": "session-a"}}}
    assert isinstance(envelope["rpcId"], str) and envelope["rpcId"]


def test_authority_defaults_to_base_url_host_and_port():
    adapter = DshAdapter(base_url="http://127.0.0.1:52713", secret="c2VjcmV0")
    assert adapter.authority == "127.0.0.1:52713"
    assert "Cookie" in adapter.headers()


class _Recorder(BaseHTTPRequestHandler):
    calls = []
    sessions = []            # rows returned by session/list
    create_id = "session-created-1"
    write_report = True
    report_path = None

    def do_POST(self):  # noqa: N802 - http.server API
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        method = body.get("method")
        _Recorder.calls.append(
            {"path": self.path, "method": method, "cookie": self.headers.get("Cookie"),
             "origin": self.headers.get("Origin"), "body": body}
        )
        if method == "session/list":
            payload = {"result": {"ok": True, "value": {"items": _Recorder.sessions}}}
        elif method == "session/create":
            payload = {"result": {"ok": True, "value": {"sessionId": _Recorder.create_id}}}
        elif method == "session/rename":
            payload = {"result": {"ok": True}}
        else:
            if _Recorder.write_report and _Recorder.report_path is not None:
                _Recorder.report_path.write_text("summary: done\nfiles: 1\nchecks: 1\n", encoding="utf-8")
            payload = {"result": {"ok": True}}
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):  # silence
        pass


@pytest.fixture()
def harness(tmp_path):
    _Recorder.calls = []
    _Recorder.sessions = []
    _Recorder.write_report = True
    _Recorder.report_path = tmp_path / "T-0001-REPORT.md"
    server = HTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield "http://127.0.0.1:%d" % server.server_address[1]
    server.shutdown()
    thread.join(timeout=2)


def _request(tmp_path, task_id="T-0001", role="backend"):
    return AdapterRequest(role=role, prompt="Add /health", cwd=str(tmp_path), task_id=task_id)


def _methods():
    return [call["method"] for call in _Recorder.calls]


def test_run_posts_to_api_with_cookie_and_reads_the_report(harness, tmp_path):
    adapter = DshAdapter(
        base_url=harness,
        sessions={"backend": "session-backend"},
        secret="c2VjcmV0LWZvci10ZXN0cw",
        wait_s=5,
        poll_s=0.05,
    )
    result = adapter.run(_request(tmp_path))
    assert result.exit_code == 0
    assert "summary: done" in result.text
    prompt_call = next(call for call in _Recorder.calls if call["method"] == "session/prompt")
    assert prompt_call["path"] == "/api/session/prompt"
    assert prompt_call["cookie"].startswith("dsh-auth-")
    assert prompt_call["origin"] == harness
    request = prompt_call["body"]["payload"]["args"]["request"]
    assert request["sessionId"] == "session-backend"
    assert request["mode"] == "queue"
    assert request["content"][0]["type"] == "text"
    assert "T-0001-REPORT.md" in request["content"][0]["text"]


def test_auto_role_reuses_an_existing_session_by_title(harness, tmp_path):
    _Recorder.sessions = [
        {"sessionId": "session-old", "updatedAt": 1, "cwd": str(tmp_path),
         "projections": {"values": {"title": "PRL 后端"}}},
        {"sessionId": "session-new", "updatedAt": 9, "cwd": str(tmp_path),
         "projections": {"values": {"title": "PRL 后端"}}},
    ]
    adapter = DshAdapter(
        base_url=harness,
        sessions={"backend": "auto"},
        role_titles={"backend": "PRL 后端"},
        wait_s=5,
        poll_s=0.05,
    )
    result = adapter.run(_request(tmp_path))
    assert result.exit_code == 0
    assert "session/list" in _methods()
    assert "session/create" not in _methods()
    sent = next(c for c in _Recorder.calls if c["method"] == "session/prompt")
    assert sent["body"]["payload"]["args"]["request"]["sessionId"] == "session-new"
    cache = json.loads((tmp_path / ".tower" / "dsh-sessions.json").read_text(encoding="utf-8"))
    assert cache["backend"] == "session-new"


def test_auto_role_creates_and_renames_when_no_title_matches(harness, tmp_path):
    adapter = DshAdapter(
        base_url=harness,
        sessions={},
        role_titles={"backend": "PRL 后端"},
        create_cwd=str(tmp_path),
        wait_s=5,
        poll_s=0.05,
    )
    result = adapter.run(_request(tmp_path))
    assert result.exit_code == 0
    assert "session/create" in _methods()
    rename = next(c for c in _Recorder.calls if c["method"] == "session/rename")
    assert rename["body"]["payload"]["args"]["request"] == {
        "sessionId": "session-created-1",
        "title": "PRL 后端",
    }
    sent = next(c for c in _Recorder.calls if c["method"] == "session/prompt")
    assert sent["body"]["payload"]["args"]["request"]["sessionId"] == "session-created-1"


def test_second_run_uses_the_cache_without_listing(harness, tmp_path):
    adapter = DshAdapter(base_url=harness, sessions={}, role_titles={"backend": "PRL 后端"},
                         wait_s=5, poll_s=0.05)
    adapter.run(_request(tmp_path))
    _Recorder.calls = []
    second = DshAdapter(base_url=harness, sessions={}, role_titles={"backend": "PRL 后端"},
                        wait_s=5, poll_s=0.05)
    second.run(_request(tmp_path, task_id="T-0002"))
    assert "session/list" not in _methods()
    sent = next(c for c in _Recorder.calls if c["method"] == "session/prompt")
    assert sent["body"]["payload"]["args"]["request"]["sessionId"] == "session-created-1"


def test_run_times_out_when_the_role_never_writes_its_report(harness, tmp_path):
    _Recorder.write_report = False
    adapter = DshAdapter(base_url=harness, sessions={"backend": "session-backend"},
                         wait_s=0.3, poll_s=0.05)
    result = adapter.run(_request(tmp_path))
    assert result.exit_code == 4
    assert "timed out" in result.text
