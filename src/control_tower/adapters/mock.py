"""Deterministic adapter used by tests and `tower demo`.

It does not call any model: it writes a small artifact inside the role's
workspace and returns a report string that mentions it. This makes the whole
dispatch -> report -> verify loop testable offline and reproducible in CI.
"""

from __future__ import annotations

import time
from pathlib import Path

from .base import AdapterRequest, AdapterResult


class MockAdapter:
    name = "mock"

    def __init__(self, sleep_s: float = 0.0, artifact_name: str = "MOCK-ARTIFACT.md", **_: object):
        self.sleep_s = sleep_s
        self.artifact_name = artifact_name

    def run(self, req: AdapterRequest) -> AdapterResult:
        if self.sleep_s:
            time.sleep(self.sleep_s)

        # v0.8 — the restate gate. The prompt decides what is being asked for, so
        # the mock answers the question that was asked and writes no artifact:
        # that absence is what proves `dispatch --restate` ran no work.
        if "## Restatement gate" in req.prompt:
            text = (
                "## Restatement\n"
                f"I am the {req.role} role and this is a restatement-only turn for task "
                f"{req.task_id}: before doing anything I must say back what the brief asks "
                "for, in my own words, so a misread is caught now instead of after a full run.\n\n"
                "## Assumptions\n"
                "- none: the brief was unambiguous as written\n\n"
                "## Done looks like\n"
                f"The brief for {req.task_id} is delivered and the report cites a check that was "
                "actually run.\n"
            )
            return AdapterResult(text=text, exit_code=0, meta={"mode": "restate"})

        role_dir = Path(req.cwd)
        role_dir.mkdir(parents=True, exist_ok=True)
        artifact = role_dir / f"{req.task_id or 'task'}-{self.artifact_name}"
        artifact.write_text(
            "# Mock artifact\n\n"
            f"role: {req.role}\n"
            f"task: {req.task_id}\n\n"
            "This file was produced by the mock adapter to prove that the\n"
            "dispatch -> report -> verify loop works without any model.\n",
            encoding="utf-8",
        )

        text = (
            "## Status\n"
            "ready\n\n"
            "## Summary\n"
            f"[mock:{req.role}] handled task {req.task_id} and wrote a placeholder artifact.\n\n"
            "## Files\n"
            f"- {artifact} — placeholder artifact written by the mock adapter\n\n"
            "## Checks\n"
            "- `os.path.exists(artifact)` → True\n"
            "- `artifact.stat().st_size > 0` → True\n\n"
            "## Open issues\n"
            "- none (this is a deterministic mock run)\n\n"
            "## Attention\n"
            "- none\n"
        )
        return AdapterResult(text=text, exit_code=0, meta={"artifact": str(artifact)})
