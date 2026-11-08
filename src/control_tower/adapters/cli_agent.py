"""Generic CLI adapter: drive any agent binary that reads a prompt on stdin.

Examples
--------
    [adapters.cli]
    command = ["claude", "-p", "{prompt_file}", "--permission-mode", "acceptEdits"]

    [adapters.cli]
    command = ["codex", "exec", "--cd", "{cwd}", "--full-auto"]

Placeholders: {prompt}, {prompt_file}, {cwd}, {role}, {task_id}.
The prompt is also written to <cwd>/.tower-prompt.md and exported as
TOWER_PROMPT_FILE so agents that prefer files can read it.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

from .base import AdapterRequest, AdapterResult


class CliAdapter:
    name = "cli"

    def __init__(self, command: list[str] | str | None = None, shell: bool = False, **_):
        if not command:
            raise ValueError("cli adapter needs adapters.cli.command")
        self.command = command
        self.shell = shell

    def _argv(self, req: AdapterRequest, prompt_file: Path) -> list[str] | str:
        fields = {
            "prompt": req.prompt,
            "prompt_file": str(prompt_file),
            "cwd": str(req.cwd),
            "role": req.role,
            "task_id": req.task_id,
        }
        if isinstance(self.command, str):
            expanded = self.command.format(**{k: shlex.quote(v) if k == "prompt" else v for k, v in fields.items()})
            return expanded
        return [part.format(**fields) for part in self.command]

    def run(self, req: AdapterRequest) -> AdapterResult:
        cwd = Path(req.cwd)
        cwd.mkdir(parents=True, exist_ok=True)
        prompt_file = cwd / ".tower-prompt.md"
        prompt_file.write_text(req.prompt, encoding="utf-8")

        env = dict(os.environ)
        env.update(req.env or {})
        env["TOWER_ROLE"] = req.role
        env["TOWER_TASK_ID"] = req.task_id
        env["TOWER_PROMPT_FILE"] = str(prompt_file)

        argv = self._argv(req, prompt_file)
        try:
            proc = subprocess.run(
                argv,
                cwd=str(cwd),
                env=env,
                shell=self.shell,
                input=req.prompt if not self.shell else None,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=req.timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            out = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
            return AdapterResult(text=out + "\n[timeout]", exit_code=124, meta={"timeout": True})

        text = (proc.stdout or "").strip()
        if proc.stderr:
            text += ("\n\n[stderr]\n" + proc.stderr.strip()[:4000])
        return AdapterResult(
            text=text,
            exit_code=proc.returncode,
            meta={"argv": argv if isinstance(argv, list) else argv},
        )
