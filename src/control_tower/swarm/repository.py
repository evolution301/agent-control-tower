"""蜂群模式持久化仓储层（Repository）。

所有状态与公开群聊消息均以纯文本文件落在 `.tower/swarm/` 下，
并使用 `tower_lock` + 原子替换写入，保证多线程并发读写不损坏数据。
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Callable

from ..board import Board, now_ts
from ..config import TowerConfig
from ..locks import tower_lock
from .models import BoardMessage, SwarmState, extract_mentions

# 进程内线程锁：与跨进程文件锁 tower_lock 配合，保证同进程多线程并行唤醒工蜂时读-改-写完全原子化
_THREAD_LOCK = threading.RLock()


def swarm_root(cfg: TowerConfig) -> Path:
    """返回并确保 `.tower/swarm/` 目录存在。"""
    root = cfg.swarm_dir
    root.mkdir(parents=True, exist_ok=True)
    return root


def state_path(cfg: TowerConfig) -> Path:
    """蜂群状态文件路径：`.tower/swarm/state.json`。"""
    return swarm_root(cfg) / "state.json"


def board_path(cfg: TowerConfig) -> Path:
    """共享消息板文件路径：`.tower/swarm/board.jsonl`。"""
    return swarm_root(cfg) / "board.jsonl"


def workspaces_dir(cfg: TowerConfig) -> Path:
    """独立工位根目录：`.tower/swarm/workspaces/`。"""
    path = swarm_root(cfg) / "workspaces"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _default_adapter_for(cfg: TowerConfig) -> str:
    """根据 tower.toml 推断蜂群默认适配器。"""
    if cfg.swarm_default_adapter:
        return cfg.swarm_default_adapter
    for role in cfg.roles.values():
        if role.adapter:
            return role.adapter
    return "mock"


def _load_state_unlocked(cfg: TowerConfig) -> SwarmState:
    path = state_path(cfg)
    if not path.exists():
        return SwarmState(
            title=f"{cfg.name} 蜂群",
            default_adapter=_default_adapter_for(cfg),
            default_model=cfg.swarm_default_model or "",
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return SwarmState.from_dict(raw)
    except (OSError, ValueError, TypeError) as exc:
        raise RuntimeError(
            f"读取蜂群状态文件失败（{path}）：{exc}。请检查文件是否损坏。"
        ) from exc


def _save_state_unlocked(cfg: TowerConfig, state: SwarmState) -> Path:
    path = state_path(cfg)
    state.updated_at = now_ts()
    payload = json.dumps(state.to_dict(), ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_suffix(f".json.tmp.{os.getpid()}.{threading.get_ident()}")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_state(cfg: TowerConfig) -> SwarmState:
    """读取蜂群状态；若尚未初始化则返回一份干净的默认状态。"""
    with _THREAD_LOCK:
        return _load_state_unlocked(cfg)


def save_state(cfg: TowerConfig, state: SwarmState) -> Path:
    """以原子方式写回 `.tower/swarm/state.json`，防止并发写坏文件。"""
    with _THREAD_LOCK:
        with tower_lock(cfg):
            return _save_state_unlocked(cfg, state)


def update_state(cfg: TowerConfig, mutator: Callable[[SwarmState], Any]) -> Any:
    """在线程锁 + 文件锁保护下原子执行「读状态 → 修改 → 落盘」，防止并发工蜂互相覆盖。"""
    with _THREAD_LOCK:
        with tower_lock(cfg):
            state = _load_state_unlocked(cfg)
            result = mutator(state)
            _save_state_unlocked(cfg, state)
            return result


def read_messages(cfg: TowerConfig, tail: int | None = None) -> list[BoardMessage]:
    """读取共享消息板历史记录；传入 tail 时仅返回最后 N 条。"""
    path = board_path(cfg)
    with _THREAD_LOCK:
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
    messages: list[BoardMessage] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            messages.append(BoardMessage.from_dict(json.loads(line)))
        except (ValueError, TypeError):
            continue
    if tail is not None and tail > 0:
        return messages[-tail:]
    return messages


def append_message(
    cfg: TowerConfig,
    author: str,
    body: str,
    *,
    kind: str = "post",
    mentions: list[str] | None = None,
    task_id: str = "",
    meta: dict[str, Any] | None = None,
) -> BoardMessage:
    """往共享消息板追加一条公开消息，自动分配递增编号 M-0001 并提取 @提及。"""
    cleaned_body = (body or "").strip()
    if not cleaned_body:
        raise ValueError("消息内容不能为空（message body cannot be empty）")
    parsed_mentions = list(mentions) if mentions is not None else extract_mentions(cleaned_body)
    path = board_path(cfg)
    with _THREAD_LOCK:
        with tower_lock(cfg):
            existing_count = 0
            if path.exists():
                existing_count = sum(
                    1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
                )
            msg = BoardMessage(
                id=f"M-{existing_count + 1:04d}",
                ts=now_ts(),
                author=author,
                body=cleaned_body,
                mentions=parsed_mentions,
                kind=kind,
                task_id=task_id,
                meta=dict(meta or {}),
            )
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(msg.to_dict(), ensure_ascii=False) + "\n")
    return msg


def record_ledger(cfg: TowerConfig, event: str, **fields: Any) -> None:
    """将蜂群关键事件同步写入控制塔主审计账本 `.tower/ledger.jsonl`。"""
    try:
        Board(cfg).log(event, **fields)
    except Exception:
        # 审计日志写入异常不应阻断蜂群核心运行，但保留安全容错
        pass
