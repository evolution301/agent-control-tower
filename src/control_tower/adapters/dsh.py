"""DeepSeek Harness (DSH) adapter.

Drives a *live DSH session* over the local harness RPC endpoint, which is what
the reference workflow used: one long-lived session per role, steered with
`session/prompt`, then the role's turn output is read back and parsed.

This is intentionally optional. If DSH is not running, use `cli` or `openai`.

Config
------
    [adapters.dsh]
    base_url = "http://127.0.0.1:65163"     # DSH Web GUI URL
    sessions = { lead = "session-...", backend = "session-...", ui = "session-..." }
    wait_s = 900
    poll_s = 5
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from .base import AdapterRequest, AdapterResult


class DshAdapter:
    name = "dsh"

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:1",
        sessions: dict[str, str] | None = None,
        wait_s: float = 900,
        poll_s: float = 5,
        **_,
    ):
        self.base_url = base_url.rstrip("/")
        self.sessions = dict(sessions or {})
        self.wait_s = float(wait_s)
        self.poll_s = float(poll_s)

    # -- low level -------------------------------------------------------
    def _rpc(self, method: str, body: dict, timeout: float = 60) -> dict:
        payload = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/rpc/{method}",
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "agent-control-tower"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:  # pragma: no cover - network dependent
            return {"error": f"http {exc.code}: {exc.read()[:200]!r}"}
        except Exception as exc:  # pragma: no cover
            return {"error": f"{type(exc).__name__}: {exc}"}

    def _session_id(self, role: str) -> str | None:
        return self.sessions.get(role)

    # -- adapter ---------------------------------------------------------
    def run(self, req: AdapterRequest) -> AdapterResult:
        sid = self._session_id(req.role)
        if not sid:
            return AdapterResult(
                text=f"[dsh] no session configured for role {req.role!r}",
                exit_code=2,
                meta={"missing_session": req.role},
            )

        prompt_file = Path(req.cwd) / ".tower-prompt.md"
        prompt_file.parent.mkdir(parents=True, exist_ok=True)
        prompt_file.write_text(req.prompt, encoding="utf-8")

        # 1) steer the session (queued so it lands at the next step boundary)
        send = self._rpc(
            "session/prompt",
            {
                "request": {
                    "sessionId": sid,
                    "requestId": str(uuid.uuid4()),
                    "mode": "queue",
                    "content": [{"type": "text", "text": req.prompt}],
                }
            },
            timeout=90,
        )
        if send.get("error"):
            return AdapterResult(text=f"[dsh] prompt failed: {send['error']}", exit_code=3)

        # 2) wait until the session goes idle again
        deadline = time.time() + self.wait_s
        last_text = ""
        while time.time() < deadline:
            time.sleep(self.poll_s)
            info = self._rpc("session/list", {"_request": {}}, timeout=60)
            items = (((info.get("json") or {}).get("result") or {}).get("value") or {}).get("items") or []
            current = next((i for i in items if i.get("sessionId") == sid), None)
            if current is None:
                last_text = "[dsh] session disappeared while waiting"
                break
            if not current.get("running"):
                break

        # 3) report: DSH sessions do not expose a single "final answer" RPC in
        #    this version, so the role is instructed (by the prompt template) to
        #    write its report to <cwd>/<task_id>-REPORT.md. We read that file.
        report_file = Path(req.cwd) / f"{req.task_id}-REPORT.md"
        if report_file.exists():
            text = report_file.read_text(encoding="utf-8")
            return AdapterResult(text=text, exit_code=0, meta={"report_file": str(report_file)})

        return AdapterResult(
            text=last_text or "[dsh] finished but no report file was written",
            exit_code=4,
            meta={"expected_report": str(report_file)},
        )
