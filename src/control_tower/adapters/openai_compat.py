"""OpenAI-compatible adapter (works with OpenAI, DeepSeek, vLLM, Ollama, ...).

A deliberately small agent loop: the model may call a `run_shell` tool inside
its own workspace directory, up to `max_steps` times, then it must finish.
That is enough for "role does a bounded piece of work and reports", without
pretending to be a full coding agent.

Config
------
    [adapters.openai]
    base_url = "https://api.openai.com/v1"
    model = "gpt-4.1-mini"
    api_key_env = "OPENAI_API_KEY"
    max_steps = 12
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from .base import AdapterRequest, AdapterResult

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": "Run a shell command inside the role workspace.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    }
]

SYSTEM_SUFFIX = (
    "\n\nYou are one role inside a control-tower workflow. Work only inside the "
    "current directory. When done, reply with a final message that starts with "
    "'SUMMARY:' followed by what you changed, the files you touched, and the "
    "checks you ran. Do not claim checks you did not run."
)


class OpenAIAdapter:
    name = "openai"

    def __init__(
        self,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4.1-mini",
        api_key_env: str = "OPENAI_API_KEY",
        api_key: str | None = None,
        max_steps: int = 12,
        **_,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key or os.environ.get(api_key_env, "")
        self.max_steps = int(max_steps)

    def _chat(self, messages: list[dict], timeout: float) -> dict:
        body = json.dumps(
            {"model": self.model, "messages": messages, "tools": TOOLS, "tool_choice": "auto"}
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": "agent-control-tower",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def run(self, req: AdapterRequest) -> AdapterResult:
        if not self.api_key:
            return AdapterResult(text="[openai] missing API key", exit_code=2)

        cwd = Path(req.cwd)
        cwd.mkdir(parents=True, exist_ok=True)
        messages: list[dict] = [
            {"role": "system", "content": f"You are the {req.role} role." + SYSTEM_SUFFIX},
            {"role": "user", "content": req.prompt},
        ]
        transcript: list[str] = []

        for step in range(self.max_steps):
            try:
                data = self._chat(messages, timeout=min(req.timeout_s, 180))
            except urllib.error.HTTPError as exc:
                return AdapterResult(text=f"[openai] http {exc.code}: {exc.read()[:300]!r}", exit_code=3)
            except Exception as exc:
                return AdapterResult(text=f"[openai] {type(exc).__name__}: {exc}", exit_code=3)

            choice = (data.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            messages.append(msg)
            content = (msg.get("content") or "").strip()
            if content:
                transcript.append(content)

            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                return AdapterResult(text=content or "(no content)", exit_code=0, meta={"steps": step + 1})

            for call in tool_calls:
                fn = (call.get("function") or {})
                name = fn.get("name")
                args = fn.get("arguments") or "{}"
                if name != "run_shell":
                    result = f"unknown tool {name}"
                else:
                    try:
                        command = json.loads(args).get("command", "")
                    except json.JSONDecodeError:
                        command = ""
                    try:
                        proc = subprocess.run(
                            command, shell=True, cwd=str(cwd), capture_output=True, text=True, timeout=120
                        )
                        result = (proc.stdout or "") + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")
                        result = result[:6000] or f"(exit {proc.returncode})"
                    except Exception as exc:  # pragma: no cover
                        result = f"error: {type(exc).__name__}: {exc}"
                messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": result})
                transcript.append(f"$ {command}\n{result[:500]}")

        return AdapterResult(
            text="\n\n".join(transcript[-3:]) or "[openai] step budget exhausted",
            exit_code=5,
            meta={"steps": self.max_steps},
        )
