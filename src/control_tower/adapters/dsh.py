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

Session resolution
------------------
A role does not need a hand-copied session id. `sessions` may map a role to an id,
to `"auto"`, or to nothing, in which case the adapter:

1. reads the cache written by the previous run (`<cwd>/.tower/dsh-sessions.json`),
2. looks the session up by title through `session/list` (`role_titles`, falling
   back to the role name), which reuses an existing conversation such as
   "PRL UI前端",
3. creates one with `session/create` in `create_cwd` and renames it
   (`session/rename`) to the role title.

The reply is read from a report file: `session/prompt` queues the turn, and the
brief tells the role to write `<task_id>-REPORT.md` into the shared workspace. DSH
exposes page reads (`session/page`) only through a live `session/follow` stream and
session transcripts are zstd-compressed on disk, so a file the role names itself is
the stable contract - the same one the reference workflow used.

Config
------
    [adapters.dsh]
    base_url = "http://127.0.0.1:52713"     # DSH desktop GUI URL
    authority = ""                          # defaults to host:port of base_url
    secret = ""                             # base64 HMAC secret; empty = anonymous
    sessions = { backend = "session-...", ui = "auto" }
    role_titles = { ui = "PRL UI前端", backend = "PRL 后端" }
    create_cwd = ""                         # creation directory for "auto" roles
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
SESSION_CACHE = Path(".tower") / "dsh-sessions.json"


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
        role_titles: dict[str, str] | None = None,
        create_cwd: str = "",
        projcache_dir: str = "",
        **_,
    ):
        self.base_url = base_url.rstrip("/")
        self.sessions = dict(sessions or {})
        self.role_titles = dict(role_titles or {})
        self.create_cwd = create_cwd
        self.projcache_dir = projcache_dir or str(
            Path.home() / ".dsh" / "storages" / "session_projcache" / "sessions"
        )
        self.wait_s = float(wait_s)
        self.poll_s = max(0.05, float(poll_s))
        self.secret = b64d(secret) if secret else b""
        self.authority = authority or self.base_url.split("://", 1)[-1].split("/", 1)[0]
        self.timezone = timezone
        self.report_file = report_file
        self.resolution: dict[str, str] = {}

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
            # The error body may never arrive (kept-alive socket); a wrong
            # secret must fail fast, not hang for the full timeout.
            try:
                exc.fp.sock.settimeout(5)
                detail = exc.read()[:300]
            except Exception:
                detail = b"<no error body>"
            return {"error": "http %s: %r" % (exc.code, detail)}
        except Exception as exc:  # pragma: no cover - network dependent
            return {"error": "%s: %s" % (type(exc).__name__, exc)}

    @staticmethod
    def _result_value(reply: dict):
        result = reply.get("result")
        if isinstance(result, dict):
            if "value" in result:
                return result["value"]
            return result
        return None

    # -- session resolution ----------------------------------------------
    def title_for(self, role: str) -> str:
        return self.role_titles.get(role) or role

    def _cache_path(self, cwd: str | Path) -> Path:
        return Path(cwd) / SESSION_CACHE

    def _read_cache(self, cwd: str | Path) -> dict:
        path = self._cache_path(cwd)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write_cache(self, cwd: str | Path, mapping: dict) -> None:
        path = self._cache_path(cwd)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:  # pragma: no cover - cache is best effort
            pass

    def find_session_by_title(self, title: str, cwd: str | Path = "") -> str | None:
        reply = self.rpc("session/list", {"_request": {}})
        value = self._result_value(reply)
        items = (value or {}).get("items") if isinstance(value, dict) else None
        if not items:
            return None
        best = None
        for item in items:
            if not isinstance(item, dict):
                continue
            projections = item.get("projections") or {}
            values = projections.get("values") if isinstance(projections, dict) else None
            item_title = (values or {}).get("title") if isinstance(values, dict) else None
            if not item_title or str(item_title).strip() != title.strip():
                continue
            item_cwd = str(item.get("cwd") or "")
            if cwd and item_cwd and Path(item_cwd) != Path(str(cwd)):
                continue
            if best is None or int(item.get("updatedAt") or 0) > int(best.get("updatedAt") or 0):
                best = item
        if best:
            return best.get("sessionId")
        # RPC rows can carry no title; the disk projection cache still has it.
        wanted = title.strip()
        for session_id, (disk_title, disk_cwd) in self.projcache_titles().items():
            if disk_title != wanted:
                continue
            if cwd and disk_cwd and Path(disk_cwd) != Path(str(cwd)):
                continue
            return session_id
        return None

    def projcache_titles(self) -> dict:
        """session id -> (title, cwd) read from the on-disk projection cache.

        `session/list` can answer with empty `projections.rows` when a row was
        never projected through the RPC (observed on a long-lived desktop host),
        while the file cache still carries `record.rows.title.val`. Reading that
        cache is how a title lookup stays working there.
        """
        root = Path(self.projcache_dir).expanduser()
        found: dict = {}
        if not root.exists():
            return found
        for file in root.glob("*.json"):
            try:
                doc = json.loads(file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            record = doc.get("record") if isinstance(doc, dict) else None
            if not isinstance(record, dict):
                continue
            identity = record.get("identity") or {}
            rows = record.get("rows") or {}
            title = None
            if isinstance(rows, dict):
                title = (rows.get("title") or {}).get("val")
            if not title:
                continue
            stem = file.stem
            session_id = stem if stem.startswith("session-") else "session-" + stem
            found[session_id] = (str(title).strip(), str(identity.get("cwd") or ""))
        return found

    def create_session(self, title: str, cwd: str | Path = "") -> str | None:
        directory = str(cwd or self.create_cwd or "")
        request = {}
        if directory:
            request["cwd"] = directory
        reply = self.rpc("session/create", {"request": request})
        if reply.get("error") and request:
            # Some hosts reject an explicit cwd (unknown path, workspace rules);
            # fall back to letting the host choose the directory.
            reply = self.rpc("session/create", {"request": {}})
        value = self._result_value(reply)
        sid = None
        if isinstance(value, dict):
            sid = value.get("sessionId") or value.get("id")
        if not sid:
            return None
        if title:
            self.rpc("session/rename", {"request": {"sessionId": sid, "title": title}})
        return sid

    def resolve_session(self, req: AdapterRequest) -> tuple[str | None, str]:
        """Return (session id, how it was resolved)."""
        explicit = self.sessions.get(req.role) or ""
        if explicit and explicit != "auto":
            return explicit, "config"

        cache = self._read_cache(req.cwd)
        cached = cache.get(req.role)
        if cached:
            return cached, "cache"

        title = self.title_for(req.role)
        found = self.find_session_by_title(title, req.cwd)
        if found:
            cache[req.role] = found
            self._write_cache(req.cwd, cache)
            return found, "title-match"

        created = self.create_session(title, str(req.cwd) if not self.create_cwd else self.create_cwd)
        if created:
            cache[req.role] = created
            self._write_cache(req.cwd, cache)
            return created, "created"
        return None, "unresolved"

    # -- adapter ---------------------------------------------------------
    def report_path(self, req: AdapterRequest) -> Path:
        name = self.report_file.format(task_id=req.task_id, role=req.role)
        return Path(req.cwd) / name

    def deliver(self, req: AdapterRequest) -> AdapterResult:
        """Queue a peer note into the role's session; never wait for a turn.

        `session/prompt` in `queue` mode returns as soon as the host accepts the
        turn, which is what makes bot-to-bot mail parallel: the sender is done
        the instant both sends (peer + archive) are handed off.
        """
        session_id, how = self.resolve_session(req)
        if not session_id:
            return AdapterResult(
                text="[dsh] no session for role %r — peer note not delivered" % req.role,
                exit_code=2,
                meta={"missing_session": req.role, "resolution": how},
            )
        reply = self.rpc(
            "session/prompt",
            {
                "request": {
                    "sessionId": session_id,
                    "requestId": str(uuid.uuid4()),
                    "mode": "queue",
                    "content": [{"type": "text", "text": req.prompt}],
                    "clientTimeZone": self.timezone,
                }
            },
            timeout=90,
        )
        if reply.get("error"):
            return AdapterResult(
                text="[dsh] queue failed: %s" % reply["error"],
                exit_code=3,
                meta={"rpc": "session/prompt", "session_id": session_id, "resolution": how},
            )
        return AdapterResult(
            text="[dsh] queued into %s" % session_id,
            exit_code=0,
            meta={"session_id": session_id, "resolution": how},
        )

    def run(self, req: AdapterRequest) -> AdapterResult:
        session_id, how = self.resolve_session(req)
        self.resolution[req.role] = how
        if not session_id:
            return AdapterResult(
                text="[dsh] could not resolve a session for role %r (config, title match and create all failed)" % req.role,
                exit_code=2,
                meta={"missing_session": req.role, "resolution": how},
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
                meta={"rpc": "session/prompt", "resolution": how},
            )
        result = reply.get("result")
        if isinstance(result, dict) and result.get("ok") is False:
            return AdapterResult(
                text="[dsh] prompt rejected: " + json.dumps(result, ensure_ascii=False)[:300],
                exit_code=3,
                meta={"rpc": "session/prompt", "result": result, "resolution": how},
            )

        deadline = time.time() + self.wait_s
        while time.time() < deadline:
            if report.exists() and report.stat().st_size > 0:
                return AdapterResult(
                    text=report.read_text(encoding="utf-8"),
                    exit_code=0,
                    meta={"report_file": str(report), "session_id": session_id, "resolution": how},
                )
            time.sleep(self.poll_s)
        return AdapterResult(
            text="[dsh] timed out after %gs waiting for %s" % (self.wait_s, report),
            exit_code=4,
            meta={"expected_report": str(report), "session_id": session_id, "resolution": how},
        )
