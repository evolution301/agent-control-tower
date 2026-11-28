"""蜂群模式核心领域模型（Models）。

负责定义工蜂（SwarmWorker）、共享消息板条目（BoardMessage）、蜂群全局状态（SwarmState），
以及工蜂命名校验和 @提及 解析等纯函数逻辑。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from ..board import now_ts

# 工蜂的 4 种核心生命周期状态（与蜂群语义严格对齐）
SWARM_STATES = ("waiting", "working", "stopped", "killed")

# 工位隔离模式：inherit 共享主目录，share 使用 git worktree 共享仓库独立副本，branch 完全隔离分支副本
WORKSPACE_MODES = ("inherit", "share", "branch")

# 合法工蜂名：小写字母/数字开头，总长 1..24，仅含 a-z 0-9 - _
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,23}$")

# 匹配消息体里的 @提及（忽略邮箱地址中 @ 前紧邻字母数字的情况）
_MENTION_RE = re.compile(r"(?<![A-Za-z0-9_.-])@([a-z0-9][a-z0-9_-]{0,23})\b")

# 默认内置模型目录（可在 tower.toml 的 swarm.models 中覆盖或追加）
DEFAULT_MODEL_CATALOG: list[dict[str, Any]] = [
    {"id": "claude-sonnet-4", "label": "Claude Sonnet 4", "adapter": "cli", "suggested": True},
    {"id": "gpt-4.1", "label": "GPT-4.1", "adapter": "openai", "suggested": True},
    {"id": "gpt-4.1-mini", "label": "GPT-4.1 Mini", "adapter": "openai", "suggested": True},
    {"id": "deepseek-chat", "label": "DeepSeek Chat", "adapter": "openai", "suggested": True},
    {"id": "deepseek-reasoner", "label": "DeepSeek Reasoner", "adapter": "openai", "suggested": False},
    {"id": "gemini-2.5-pro", "label": "Gemini 2.5 Pro", "adapter": "cli", "suggested": True},
    {"id": "mock-default", "label": "Mock Default (Offline)", "adapter": "mock", "suggested": True},
    {"id": "mock-pro", "label": "Mock Pro (Offline)", "adapter": "mock", "suggested": False},
]


def validate_worker_name(name: str) -> str:
    """校验工蜂花名册名称是否合法，不合法时抛出可读的中文+英文错误说明。"""
    cleaned = (name or "").strip()
    if not cleaned:
        raise ValueError("工蜂名称不能为空（worker name cannot be empty）")
    if cleaned == "all" or cleaned == "coordinator" or cleaned.startswith("coordinator-"):
        raise ValueError(
            f"工蜂名称 {cleaned!r} 为系统保留词（不能使用 all、coordinator 或 coordinator-*）"
        )
    if not _NAME_RE.match(cleaned):
        raise ValueError(
            f"工蜂名称 {cleaned!r} 格式不合法：必须以小写字母或数字开头，长度 1-24，仅允许 a-z、0-9、-、_"
        )
    return cleaned


def validate_workspace_mode(mode: str) -> str:
    """校验工位隔离模式是否合法。"""
    cleaned = (mode or "inherit").strip().lower()
    if cleaned not in WORKSPACE_MODES:
        raise ValueError(
            f"未知的工位隔离模式 {mode!r}；仅支持: {', '.join(WORKSPACE_MODES)}"
        )
    return cleaned


def extract_mentions(text: str) -> list[str]:
    """从消息正文中提取去重后的 @提及 目标列表（保持出现顺序）。"""
    seen: list[str] = []
    for match in _MENTION_RE.finditer(text or ""):
        token = match.group(1)
        if token not in seen:
            seen.append(token)
    return seen


@dataclass
class SwarmWorker:
    """代表蜂群里的一只工蜂（包含身份、模型、工位、记忆与验收状态）。"""

    name: str
    brief: str = ""
    adapter: str = "mock"
    model: str = ""
    state: str = "waiting"
    workspace_mode: str = "inherit"
    workspace_path: str = ""
    branch_name: str = ""
    fallback_copy: bool = False
    parent: str = "coordinator"
    forked_from: str = ""
    task_id: str = ""
    last_files: list[str] = field(default_factory=list)
    last_summary: str = ""
    verified: bool | None = None
    verify_note: str = ""
    merged: bool = False
    history: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=now_ts)
    updated_at: str = field(default_factory=now_ts)
    last_active_at: str = field(default_factory=now_ts)
    killed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SwarmWorker":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})

    def summary_view(self) -> dict[str, Any]:
        """返回面向状态看板与 CLI 的轻量摘要（不含冗长 history 全文）。"""
        return {
            "name": self.name,
            "adapter": self.adapter,
            "model": self.model or "(default)",
            "state": self.state,
            "workspace_mode": self.workspace_mode,
            "workspace_path": self.workspace_path,
            "branch_name": self.branch_name,
            "fallback_copy": self.fallback_copy,
            "parent": self.parent,
            "forked_from": self.forked_from,
            "task_id": self.task_id,
            "brief": self.brief,
            "last_summary": self.last_summary,
            "last_files": list(self.last_files),
            "verified": self.verified,
            "verify_note": self.verify_note,
            "merged": self.merged,
            "turns": len(self.history),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_active_at": self.last_active_at,
            "killed_at": self.killed_at,
        }


@dataclass
class BoardMessage:
    """共享消息板里的一条公开消息。"""

    id: str
    ts: str
    author: str
    body: str
    mentions: list[str] = field(default_factory=list)
    kind: str = "post"
    task_id: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BoardMessage":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class SwarmState:
    """整个蜂群的持久化快照状态。"""

    title: str = ""
    default_adapter: str = "mock"
    default_model: str = ""
    created_at: str = field(default_factory=now_ts)
    updated_at: str = field(default_factory=now_ts)
    workers: dict[str, SwarmWorker] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "default_adapter": self.default_adapter,
            "default_model": self.default_model,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "workers": {name: w.to_dict() for name, w in self.workers.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SwarmState":
        workers_raw = data.get("workers") or {}
        workers = {
            name: SwarmWorker.from_dict(spec)
            for name, spec in workers_raw.items()
            if isinstance(spec, dict)
        }
        return cls(
            title=str(data.get("title") or ""),
            default_adapter=str(data.get("default_adapter") or "mock"),
            default_model=str(data.get("default_model") or ""),
            created_at=str(data.get("created_at") or now_ts()),
            updated_at=str(data.get("updated_at") or now_ts()),
            workers=workers,
        )
