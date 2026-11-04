"""Adapter contract.

An adapter is the only thing that knows how to make a role *do* something.
Adapters receive a fully-rendered prompt and a working directory, and return
the role's final text. They must not mutate the tower's state.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass
class AdapterRequest:
    role: str
    prompt: str
    cwd: Path
    timeout_s: int = 1800
    env: dict[str, str] | None = None
    task_id: str = ""
    options: dict[str, Any] | None = None


@dataclass
class AdapterResult:
    text: str
    exit_code: int = 0
    meta: dict[str, Any] | None = None


class Adapter(Protocol):
    name: str

    def run(self, req: AdapterRequest) -> AdapterResult: ...


def get_adapter(name: str, options: dict[str, Any] | None = None) -> Adapter:
    """Resolve an adapter by name from config/adapters options."""
    opts = dict(options or {})
    if name == "mock":
        from .mock import MockAdapter

        return MockAdapter(**opts)
    if name == "cli":
        from .cli_agent import CliAdapter

        return CliAdapter(**opts)
    if name == "dsh":
        from .dsh import DshAdapter

        return DshAdapter(**opts)
    if name == "openai":
        from .openai_compat import OpenAIAdapter

        return OpenAIAdapter(**opts)
    raise ValueError(
        f"unknown adapter {name!r}; available: mock, cli, dsh, openai"
    )
