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

    def deliver(self, req: AdapterRequest) -> AdapterResult:
        """Queue a peer note into the role's session without waiting for a turn.

        Fire-and-forget by contract: exit_code 0 means the message is in the
        role's queue (or an equivalent inbox), not that it was read or answered.
        Adapters that cannot queue may return a non-zero exit code; the caller
        records the failure and moves on.
        """
        ...


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
