"""DeepSeek Harness (DSH) adapter.

Drives *live DSH sessions* - one long-lived session per role, steered with
`session/prompt`, exactly like the reference workflow: the lead queues a brief,
the role works in its own session, then the role's report is read back, verified
and accepted or sent back to rework.

Transport
---------
The desktop harness exposes `<base_url>/api/<method>` and authenticates every call
with an HMAC-signed cookie
``dsh-auth-<b64u(sha256(authority))>=v1.<b64u(payload)>.<b64u(hmac(secret, payload))>``
where the payload carries the authority and an expiry. Plain ``POST /rpc/<method>``
is not accepted by the desktop host, which is why an unauthenticated bridge fails
with 401. The cookie is built from `secret` (the base64 secret the harness prints
for the bridge, or one you mint yourself); without it the call is anonymous and
only works against an auth-disabled host.

The reply is read from a report file: `session/prompt` queues the turn, and the
brief tells the role to write `<task_id>-REPORT.md` into the shared workspace.
DSH exposes page reads (`session/page`) only through a live `session/follow`
stream, and session transcripts are zstd-compressed on disk, so a file the role
names itself is the stable contract - the same one the reference workflow used.

Config
------
    [adapters.dsh]
    base_url = "http://127.0.0.1:52713"     # DSH desktop GUI URL
    authority = ""                          # defaults to host:port of base_url
    secret = ""                             # base64 HMAC secret; empty = anonymous
    sessions = { backend = "session-...", ui = "session-..." }
    wait_s = 1800                           # max wait for the report file
    poll_s = 5
    report_file = "{task_id}-REPORT.md"
    timezone = "Asia/Shanghai"
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from .base import AdapterRequest, AdapterResult

DEFAULT_TIMEZONE = "Asia/Shanghai"
COOKIE_LIFETIME_MS = 30 * 24 * 3600 * 1000


def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def b64d(text: str) -> bytes:
    padded = text + "=" * ((4 - len(text) % 4) % 4)
    return base64.urlsafe_b64decode(padded.replace("-", "+").replace("_", "/"))


def auth_cookie(authority: str, secret: bytes, now_ms: int | None = None, lifetime_ms: int = COOKIE_LIFETIME_MS) -> str:
    """Build the HMAC cookie the desktop host accepts for `authority`."""
    issued = int(time.time() * 1000) if now_ms is None else int(now_ms)
    payload = {
        "version": 1,
        "authority": authority,
        "issuedAt": issued,
        "expiresAt": issued + int(lifetime_ms),
    }
    body = b64u(json.dumps(payload, separators=(",", ":"), sort_keys=False).encode("utf-8"))
    signature = b64u(hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest())
    name = "dsh-auth-" + b64u(hashlib.sha256(authority.encode("utf-8")).digest())
    return "%s=v1.%s.%s" % (name, body, signature)


class DshAdapter:
    name = "dsh"

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:1",
        sessions: dict[str, str] | None = None,
        wait_s: float = 1800,
        poll_s: float = 5,
        secret: str = "",
        authority: str = "",
        timezone: str = DEFAULT_TIMEZONE,
        report_file: str = "{task_id}-REPORT.md",
        **_,
    ):
        self.base_url = base_url.rstrip("/")
        self.sessions = dict(sessions or {})
        self.wait_s = float(wait_s)
        self.poll_s = max(0.05, float(poll_s))
        self.secret = b64d(secret) if secret else b""
        self.authority = authority or self.base_url.split("://", 1)[-1].split("/", 1)[0]
        self.timezone = timezone
        self.report_file = report_file

    # -- low level -------------------------------------------------------
    def envelope(self, method: str, args: dict) -> dict:
        """The client-request wrapper every `/api/<method>` call expects."""
        return {
            "type": "client-request",
            "rpcId": str(uuid.uuid4()),
            "method": method,
            "payload": {"args": args},
        }

    def headers(self) -> dict:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Origin": self.base_url,
            "User-Agent": "agent-control-tower",
        }
        if self.secret:
            headers["Cookie"] = auth_cookie(self.authority, self.secret)
        return headers

    def rpc(self, method: str, args: dict, timeout: float = 90) -> dict:
        data = json.dumps(self.envelope(method, args)).encode("utf-8")
        request = urllib.request.Request(
            "%s/api/%s" % (self.base_url, method),
            data=data,
            headers=self.headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8") or "{}"
                return json.loads(raw)
        except urllib.error.HTTPError as exc:  # pragma: no cover - network dependent
            detail = exc.read()[:300]
            return {"error": "http %s: %r" % (exc.code, detail)}
        except Exception as exc:  # pragma: no cover - network dependent
            return {"error": "%s: %s" % (type(exc).__name__, exc)}

    # -- adapter ---------------------------------------------------------
    def report_path(self, req: AdapterRequest) -> Path:
        name = self.report_file.format(task_id=req.task_id, role=req.role)
        return Path(req.cwd) / name

    def run(self, req: AdapterRequest) -> AdapterResult:
        session_id = self.sessions.get(req.role)
        if not session_id:
            return AdapterResult(
                text="[dsh] no session configured for role %r" % req.role,
                exit_code=2,
                meta={"missing_session": req.role},
            )

        report = self.report_path(req)
        report.parent.mkdir(parents=True, exist_ok=True)
        if report.exists():
            report.unlink()

        prompt = (
            req.prompt
            + "\n\n[control tower] 完成后把回执写到 "
            + str(report)
            + " （覆盖写，包含 summary / files / checks），并回复一行摘要。"
        )
        reply = self.rpc(
            "session/prompt",
            {
                "request": {
                    "sessionId": session_id,
                    "requestId": str(uuid.uuid4()),
                    "mode": "queue",
                    "content": [{"type": "text", "text": prompt}],
                    "clientTimeZone": self.timezone,
                }
            },
            timeout=90,
        )
        if reply.get("error"):
            return AdapterResult(
                text="[dsh] prompt failed: %s" % reply["error"],
                exit_code=3,
                meta={"rpc": "session/prompt"},
            )
        result = reply.get("result")
        if isinstance(result, dict) and result.get("ok") is False:
            return AdapterResult(
                text="[dsh] prompt rejected: " + json.dumps(result, ensure_ascii=False)[:300],
                exit_code=3,
                meta={"rpc": "session/prompt", "result": result},
            )

        deadline = time.time() + self.wait_s
        while time.time() < deadline:
            if report.exists() and report.stat().st_size > 0:
                return AdapterResult(
                    text=report.read_text(encoding="utf-8"),
                    exit_code=0,
                    meta={"report_file": str(report)},
                )
            time.sleep(self.poll_s)
        return AdapterResult(
            text="[dsh] timed out after %gs waiting for %s" % (self.wait_s, report),
            exit_code=4,
            meta={"expected_report": str(report)},
        )
