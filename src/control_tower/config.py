"""Configuration loading and workspace layout.

A tower lives in one directory (the "workspace root"). Everything the tower
knows is on disk, so a run can be inspected, resumed, or diffed with git.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_NAME = "tower.toml"
STATE_DIR = ".tower"


@dataclass
class Role:
    name: str
    description: str = ""
    adapter: str = "mock"
    prompt_file: str | None = None
    write_scopes: list[str] = field(default_factory=list)
    max_rounds: int = 1
    timeout_s: int = 1800
    env: dict[str, str] = field(default_factory=dict)
    # "" inherits the tower-level dispatch_mode (docs: dispatch --async / tower worker)
    dispatch_mode: str = ""


@dataclass
class TowerConfig:
    name: str = "control-tower"
    workspace: Path = Path(".")
    # Where the actual project lives, when it is not the tower workspace (a
    # tower often coordinates work in a sibling checkout). Report file paths and
    # relative verify commands resolve against this too.
    project_root: Path = Path(".")
    # cwd for verify_commands; empty = workspace. Set this when the checks live
    # in the project (e.g. node scripts/lint.mjs) instead of the tower dir.
    verify_cwd: str = ""
    roles: dict[str, Role] = field(default_factory=dict)
    adapter_options: dict[str, dict[str, Any]] = field(default_factory=dict)
    serial_requests: bool = False
    request_gap_s: float = 1.5
    # The tower closes the ordinary follow-up loop itself (verify → accept, or
    # rework with its own failure reasons). A person is asked only when the tower
    # has run out of moves.
    auto_triage: bool = True
    # "sync" (default, unchanged behaviour) or "async": dispatch hands the task to
    # the queue and returns the id in under a second; a `tower worker` process runs
    # the adapter and settles the task. Per-role override via roles.<name>.dispatch_mode
    # or the async_roles list.
    dispatch_mode: str = "sync"
    # background worker pool inside one `tower worker` process (design doc §1: N=4)
    workers: int = 4
    # settlement notice: "inbox" (always written) and optionally a lead session push
    notify_kind: str = "inbox"          # inbox | lead_session
    notify_session: str = ""            # required when notify_kind == lead_session
    async_roles: list[str] = field(default_factory=list)
    # inject the pstack-derived work-discipline index into every dispatch prompt
    principles: bool = True
    # v0.10 — the self-review loop (pstack): reports must carry `## Self-review`,
    # and accepted-quality reports face an adversarial review pass before accept.
    self_review: bool = True
    review_roles: list[str] = field(default_factory=lambda: ["auditor"])
    review_budget: int = 1
    # B3: a claimed/in_progress task older than this (minutes) is marked
    # ⧗stale on `tower status` — free it with `tower unstick <id>`.
    staleness_minutes: int = 30
    # B11: a pending/reported/failed task older than this is 陈旧 (stale): it is
    # listed separately and no longer inflates 「待总控」.
    stale_hours: float = 12.0
    # 2026-11-10: a pending job older than this (minutes) while no worker consumes
    # it is surfaced on `tower status` / `tower inbox` / `tower doctor`.
    queue_alert_minutes: int = 15
    # v0.16 — deploy guard: how to read a remote file before an upload.
    # `deploy.fetch` in tower.toml, a list (placeholders expanded) or a shell string.
    deploy_fetch: list[str] | str | None = None
    # B16: reports created before this timestamp skip the self-review gate.
    self_review_since: str = ""
    # v0.12 — issue escalation: blocked/failed jobs go to a dedicated issues bot
    # instead of stacking up in 「需要你」. Role must exist in this tower.
    escalation_enabled: bool = True
    escalation_role: str = "issues"
    escalation_max_attempts: int = 1
    # shown on the control-room top bar; empty means "local operator"
    account: str = ""
    # where the tower file lives — the worker re-parses it per job so role edits
    # made after the detached worker started are picked up without a restart
    config_path: str = ""
    accept_requires: list[str] = field(
        default_factory=lambda: ["summary", "files", "checks"]
    )
    verify_commands: list[str] = field(default_factory=list)

    @property
    def state_dir(self) -> Path:
        return self.workspace / STATE_DIR

    @property
    def tasks_dir(self) -> Path:
        return self.state_dir / "tasks"

    @property
    def ledger_path(self) -> Path:
        return self.state_dir / "ledger.jsonl"

    @property
    def inbox_path(self) -> Path:
        return self.state_dir / "inbox.jsonl"

    @property
    def queue_dir(self) -> Path:
        return self.state_dir / "queue"

    def effective_dispatch_mode(self, role_name: str = "") -> str:
        """sync | async — role override wins over async_roles, then the tower default."""
        role = self.roles.get(role_name)
        if role and role.dispatch_mode:
            return role.dispatch_mode if role.dispatch_mode in ("sync", "async") else "sync"
        if role_name in self.async_roles:
            return "async"
        return self.dispatch_mode if self.dispatch_mode in ("sync", "async") else "sync"

    @property
    def roles_dir(self) -> Path:
        """Per-role scratch space. Roles may only write below their own folder."""
        return self.workspace / "roles"

    def role_dir(self, role: str) -> Path:
        return self.roles_dir / role

    def task_dir(self, task_id: str) -> Path:
        return self.tasks_dir / task_id

    @property
    def conversations_dir(self) -> Path:
        """Bot-to-bot threads. Every peer message is filed here, not whispered."""
        return self.state_dir / "conversations"

    @property
    def huddles_dir(self) -> Path:
        return self.state_dir / "huddles"


def _load_yaml_or_toml(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix in (".yaml", ".yml"):
        return yaml.safe_load(text) or {}
    if path.suffix == ".json":
        import json

        return json.loads(text)
    # tower.toml is written as YAML on purpose: one format, fewer deps.
    return yaml.safe_load(text) or {}


CONFIG_NAMES = (DEFAULT_CONFIG_NAME, "tower.yaml", "tower.yml", "tower.json")


def find_config_path(start: Path | str | None = None) -> Path:
    """The tower config file for a workspace, found by walking up from *start*.

    `parse_config` reads YAML and JSON alike, and the DSH bundle's workspace check
    accepts `tower.json` as well, so the same four names are searched here.
    Writing tools (roles) need the path itself, not the parsed config.
    """
    here = Path(start or os.getcwd()).resolve()
    for candidate in (here, *here.parents):
        for name in CONFIG_NAMES:
            path = candidate / name
            if path.exists():
                return path
    raise FileNotFoundError(
        f"no {DEFAULT_CONFIG_NAME} found in {here} or any parent directory"
    )


def load_config(start: Path | str | None = None) -> TowerConfig:
    """Find and parse the tower config for a workspace."""
    return parse_config(find_config_path(start))


def parse_config(path: Path) -> TowerConfig:
    raw = _load_yaml_or_toml(path)
    workspace = Path(raw.get("workspace") or path.parent).expanduser()
    if not workspace.is_absolute():
        workspace = (path.parent / workspace).resolve()

    adapter_options = raw.get("adapters") or {}

    project_root = Path(raw.get("project_root") or workspace).expanduser()
    if not project_root.is_absolute():
        project_root = (path.parent / project_root).resolve()

    roles: dict[str, Role] = {}
    for name, spec in (raw.get("roles") or {}).items():
        spec = spec or {}
        roles[name] = Role(
            name=name,
            description=spec.get("description", ""),
            adapter=spec.get("adapter", "mock"),
            prompt_file=spec.get("prompt_file"),
            write_scopes=list(spec.get("write_scopes") or []),
            max_rounds=int(spec.get("max_rounds", 1)),
            timeout_s=int(spec.get("timeout_s", 1800)),
            env=dict(spec.get("env") or {}),
            dispatch_mode=str(spec.get("dispatch_mode", "")).strip().lower(),
        )

    if not roles:
        raise ValueError(f"{path}: at least one role must be defined")

    return TowerConfig(
        name=raw.get("name", path.parent.name),
        workspace=workspace,
        project_root=project_root,
        verify_cwd=str(raw.get("verify_cwd") or ""),
        roles=roles,
        adapter_options=adapter_options,
        serial_requests=bool(raw.get("serial_requests", False)),
        request_gap_s=float(raw.get("request_gap_s", 1.5)),
        auto_triage=bool(raw.get("auto_triage", True)),
        account=str(raw.get("account", "")),
        dispatch_mode=str(raw.get("dispatch_mode", "sync")).strip().lower(),
        workers=max(1, min(16, int(raw.get("workers", 4)))),
        notify_kind=str((raw.get("notify") or {}).get("kind", "inbox")).strip().lower(),
        notify_session=str((raw.get("notify") or {}).get("session", "")),
        async_roles=[str(r) for r in (raw.get("async_roles") or [])],
        principles=bool(raw.get("principles", True)),
        self_review=bool(raw.get("self_review", True)),
        review_roles=[str(r) for r in (raw.get("review_roles") or ["auditor"])],
        review_budget=max(0, int(raw.get("review_budget", 1))),
        staleness_minutes=max(1, int(raw.get("staleness_minutes", 30))),
        stale_hours=float(raw.get("stale_hours", 12.0)),
        queue_alert_minutes=max(1, int(raw.get("queue_alert_minutes", 15))),
        deploy_fetch=(raw.get("deploy") or {}).get("fetch") or None,
        self_review_since=str(raw.get("self_review_since", "")),
        escalation_enabled=bool((raw.get("escalation") or {}).get("enabled", True)),
        escalation_role=str((raw.get("escalation") or {}).get("role", "issues")).strip(),
        escalation_max_attempts=max(1, int((raw.get("escalation") or {}).get("max_attempts", 1))),
        config_path=str(path),
        accept_requires=list(raw.get("accept_requires") or ["summary", "files", "checks"]),
        verify_commands=list(raw.get("verify_commands") or []),
    )


def reload_config(cfg: TowerConfig) -> TowerConfig:
    """Re-parse the tower file (the detached worker picks up role edits live).

    Returns the same cfg when the source path is unknown or unreadable — the
    worker keeps running with what it has instead of dying on a bad edit.
    """
    path = getattr(cfg, "config_path", "") or ""
    if not path:
        return cfg
    try:
        return parse_config(Path(path))
    except (OSError, ValueError):
        return cfg


def ensure_layout(cfg: TowerConfig) -> None:
    for d in (cfg.state_dir, cfg.tasks_dir, cfg.roles_dir, cfg.conversations_dir, cfg.huddles_dir):
        d.mkdir(parents=True, exist_ok=True)
    for role in cfg.roles:
        cfg.role_dir(role).mkdir(parents=True, exist_ok=True)


_ID_RE = re.compile(r"^T-\d{4,}$")


def next_task_id(cfg: TowerConfig) -> str:
    ensure_layout(cfg)
    existing = []
    for p in cfg.tasks_dir.glob("T-*"):
        stem = p.stem  # T-0001.json -> T-0001
        if _ID_RE.match(stem):
            existing.append(int(stem.split("-")[1]))
    return f"T-{(max(existing) + 1) if existing else 1:04d}"
