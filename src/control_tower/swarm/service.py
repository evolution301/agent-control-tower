"""蜂群核心调度服务（Swarm Service）。

把蜂群的「公开共享消息板 + @唤醒 + 弹性招人/分片/克隆记忆 + 独立工位 + 无损换模型 + 巡检看门狗」
与控制塔原有的「文件实查 + verify_commands 重跑 + 审计账本」打通为统一闭环。
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from ..adapters import AdapterRequest, get_adapter
from ..board import now_ts
from ..config import TowerConfig
from ..snapshot import age_s
from .models import (
    DEFAULT_MODEL_CATALOG,
    BoardMessage,
    SwarmState,
    SwarmWorker,
    extract_mentions,
    validate_worker_name,
    validate_workspace_mode,
)
from .repository import (
    append_message,
    load_state,
    read_messages,
    record_ledger,
    save_state,
    update_state,
)
from .workspace import (
    changed_files_in_workspace,
    cleanup_workspace,
    merge_workspace,
    prepare_workspace,
)

# 渲染给工蜂的蜂群工作提示词模板
SWARM_TURN_PROMPT = """## Swarm message board — {title}

You are worker **{name}** in a hybrid Swarm + Control Tower team.
- Workspace directory: `{workspace_path}` (mode: `{workspace_mode}`)
- Pinned model: `{model}` · Adapter: `{adapter}`
- Added by: `{parent}`

### Your original assignment
{brief}

### Recent public board messages
{recent_board}

### Triggering message (from `{author}`)
{trigger_body}

### Instructions
1. Work strictly inside `{workspace_path}`.
2. If you create or edit files, list every path clearly under `## Files` (`- <path> — <what changed>`).
3. If you ran checks/commands, list the real command and observed output under `## Checks`.
4. To hand off or ask another member, mention them with `@<member_name>` in your reply.
5. Keep your reply concise and factual — it will be posted publicly to the shared message board.
"""

_FILE_LINE_RE = re.compile(r"^\s*[-*]\s+`?([^\s`—–]+)`?")


def _parse_reported_files(text: str) -> list[str]:
    """从工蜂回复的 `## Files` 段落提取声明的交付文件路径（完整保留路径中的连字符 `-`）。"""
    files: list[str] = []
    in_files = False
    for raw_line in (text or "").splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("## "):
            in_files = stripped.lower().startswith("## files")
            continue
        if not in_files or not stripped:
            continue
        if stripped.lower() in ("- none", "none", "- 无", "无"):
            continue
        # 先按 " — " / " – " / " - " 拆出说明文字，只保留前半段路径
        head = re.split(r"\s+[—–-]\s+", stripped, maxsplit=1)[0]
        match = _FILE_LINE_RE.match(head)
        if match:
            candidate = match.group(1).strip().strip(",")
            if candidate and candidate not in files:
                files.append(candidate)
    return files


def _resolve_model_and_adapter(
    cfg: TowerConfig,
    state: SwarmState,
    requested_model: str = "",
    requested_adapter: str = "",
) -> tuple[str, str]:
    """解析模型与适配器：支持 `adapter:model` 写法，也支持按模型目录自动反查适配器。"""
    model = (requested_model or "").strip()
    adapter = (requested_adapter or "").strip().lower()

    if ":" in model and not adapter:
        prefix, rest = model.split(":", 1)
        if prefix.strip().lower() in ("mock", "cli", "openai", "dsh"):
            adapter = prefix.strip().lower()
            model = rest.strip()

    catalog = list_models(cfg, all_models=True)["models"]
    if model and not adapter:
        for entry in catalog:
            if entry["id"].lower() == model.lower() or entry["label"].lower() == model.lower():
                model = entry["id"]
                # 仅当目标适配器在当前 tower 中可用时自动采用
                cand = entry.get("adapter", "")
                if cand == "mock" or cand in (cfg.adapter_options or {}):
                    adapter = cand
                break

    if not adapter:
        adapter = state.default_adapter or "mock"
    if not model:
        model = state.default_model or (
            "mock-default" if adapter == "mock" else ""
        )
    return adapter, model


def list_models(cfg: TowerConfig, *, all_models: bool = False) -> dict[str, Any]:
    """列出可选模型目录（支持 tower.toml 中 swarm.models 自定义覆盖）。"""
    merged: dict[str, dict[str, Any]] = {}
    for item in DEFAULT_MODEL_CATALOG:
        merged[item["id"]] = dict(item)

    # 读取 adapters.openai.model 等已有配置，自动加入目录
    openai_opts = (cfg.adapter_options or {}).get("openai") or {}
    if openai_opts.get("model"):
        mid = str(openai_opts["model"])
        merged[mid] = {"id": mid, "label": mid, "adapter": "openai", "suggested": True}

    for custom in cfg.swarm_models or []:
        if isinstance(custom, str) and custom.strip():
            mid = custom.strip()
            merged[mid] = {"id": mid, "label": mid, "adapter": cfg.swarm_default_adapter or "openai", "suggested": True}
        elif isinstance(custom, dict) and custom.get("id"):
            mid = str(custom["id"]).strip()
            merged[mid] = {
                "id": mid,
                "label": str(custom.get("label") or mid),
                "adapter": str(custom.get("adapter") or cfg.swarm_default_adapter or "openai").strip().lower(),
                "suggested": bool(custom.get("suggested", True)),
            }

    models = list(merged.values())
    if not all_models:
        models = [m for m in models if m.get("suggested", True)]

    state = load_state(cfg)
    return {
        "default_adapter": state.default_adapter,
        "default_model": state.default_model,
        "all": all_models,
        "models": models,
    }


def set_title(cfg: TowerConfig, title: str, *, actor: str = "coordinator") -> dict[str, Any]:
    """设置蜂群标题（仅总调度 coordinator 可调用）。"""
    if actor != "coordinator":
        raise PermissionError("只有总调度（coordinator）可以修改蜂群标题")
    cleaned = (title or "").strip()
    if not cleaned:
        raise ValueError("蜂群标题不能为空")
    state = load_state(cfg)
    state.title = cleaned
    save_state(cfg, state)
    msg = append_message(
        cfg,
        author="coordinator",
        body=f"已将蜂群标题设置为：「{cleaned}」",
        kind="system",
    )
    record_ledger(cfg, "swarm_title", title=cleaned)
    return {"ok": True, "title": cleaned, "message": msg.to_dict()}


def _format_recent_board(messages: list[BoardMessage], limit: int = 12) -> str:
    """将最近若干条公开群聊消息渲染为工蜂可读的上下文摘录。"""
    if not messages:
        return "(no earlier messages)"
    lines = []
    for m in messages[-limit:]:
        snippet = m.body.replace("\n", " ").strip()[:240]
        lines.append(f"- [{m.id}] {m.author} ({m.kind}): {snippet}")
    return "\n".join(lines)


def _execute_worker_turn(
    cfg: TowerConfig,
    worker_name: str,
    trigger_author: str,
    trigger_body: str,
    *,
    is_resume: bool = False,
) -> dict[str, Any]:
    """唤醒单只工蜂执行一轮任务，完成后将回执发回公开消息板并让其回到 waiting 休眠态。"""

    def _mark_working(st: SwarmState) -> tuple[str, SwarmWorker]:
        w = st.workers.get(worker_name)
        if w is None:
            raise KeyError(f"找不到工蜂 {worker_name!r}")
        if w.state == "killed":
            raise ValueError(f"工蜂 {worker_name!r} 已永久解散（killed），不能再被唤醒，请重新 add")
        w.state = "working"
        w.updated_at = now_ts()
        w.last_active_at = w.updated_at
        return st.title or cfg.name, SwarmWorker.from_dict(w.to_dict())

    # 原子切换为 working 并获取快照副本，避免并行工蜂互相覆盖状态
    swarm_title, worker = update_state(cfg, _mark_working)

    recent_board = _format_recent_board(read_messages(cfg, tail=12))
    prompt = SWARM_TURN_PROMPT.format(
        title=swarm_title,
        name=worker.name,
        workspace_path=worker.workspace_path or str(cfg.workspace),
        workspace_mode=worker.workspace_mode,
        model=worker.model or "(default)",
        adapter=worker.adapter,
        parent=worker.parent,
        brief=worker.brief or "(none)",
        recent_board=recent_board,
        author=trigger_author,
        trigger_body=trigger_body,
    )

    adapter_opts = dict((cfg.adapter_options or {}).get(worker.adapter) or {})
    if worker.model:
        adapter_opts["model"] = worker.model
    adapter = get_adapter(worker.adapter, adapter_opts)

    req = AdapterRequest(
        role=worker.name,
        prompt=prompt,
        cwd=Path(worker.workspace_path or cfg.workspace),
        timeout_s=1800,
        task_id=f"SWARM-{worker.name}",
        options={
            **adapter_opts,
            "swarm": True,
            "model": worker.model,
            "history": list(worker.history[-20:]),
            "is_resume": is_resume,
            "expect_report": False,
        },
    )

    try:
        result = adapter.run(req)
        reply_text = (result.text or "").strip() or "(empty reply)"
        exit_code = int(result.exit_code)
    except Exception as exc:
        reply_text = f"[error] 工蜂 {worker.name} 执行异常：{type(exc).__name__}: {exc}"
        exit_code = 1

    reported_files = _parse_reported_files(reply_text)

    def _finish_turn(st: SwarmState) -> tuple[str, str]:
        w = st.workers[worker_name]
        now = now_ts()
        w.history.append({"ts": now, "role": "user", "content": trigger_body, "author": trigger_author})
        w.history.append({"ts": now, "role": "assistant", "content": reply_text, "model": w.model})
        if reported_files:
            w.last_files = reported_files
        w.last_summary = reply_text.splitlines()[0][:240]
        w.state = "waiting"
        w.updated_at = now
        w.last_active_at = now
        return w.model, w.adapter

    active_model, active_adapter = update_state(cfg, _finish_turn)

    posted = append_message(
        cfg,
        author=worker_name,
        body=reply_text,
        kind="reply",
        meta={"model": active_model, "adapter": active_adapter, "exit_code": exit_code},
    )
    record_ledger(
        cfg,
        "swarm_reply",
        worker=worker_name,
        model=active_model,
        exit_code=exit_code,
        message=posted.id,
    )
    return {
        "worker": worker_name,
        "exit_code": exit_code,
        "model": active_model,
        "message": posted.to_dict(),
        "mentions": posted.mentions,
    }


def _wake_workers_parallel(
    cfg: TowerConfig,
    targets: list[tuple[str, str, str]],
    *,
    is_resume: bool = False,
) -> list[dict[str, Any]]:
    """并行唤醒多只工蜂（每个工蜂有独立线程，互不阻塞）。"""
    if not targets:
        return []
    if len(targets) == 1:
        name, author, body = targets[0]
        return [_execute_worker_turn(cfg, name, author, body, is_resume=is_resume)]

    max_workers = min(max(1, cfg.workers), len(targets))
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_execute_worker_turn, cfg, name, author, body, is_resume=is_resume): name
            for name, author, body in targets
        }
        for fut in as_completed(futures):
            results.append(fut.result())
    # 按调用顺序排齐输出，方便阅读和测试断言
    order = {name: idx for idx, (name, _, _) in enumerate(targets)}
    results.sort(key=lambda r: order.get(r["worker"], 999))
    return results


def add_workers(
    cfg: TowerConfig,
    name: str,
    brief: str,
    *,
    model: str = "",
    adapter: str = "",
    workspace: str = "",
    fork: str = "",
    n: int = 1,
    tasks: list[str] | None = None,
    actor: str = "coordinator",
    auto_wake: bool = True,
) -> dict[str, Any]:
    """向蜂群召唤 1 只或一批工蜂（支持 n 份同构分身、tasks 切片并行、fork 继承记忆、独立工位）。"""
    cleaned_brief = (brief or "").strip()
    if not cleaned_brief:
        raise ValueError("派活说明（brief）不能为空，必须写清这只工蜂负责什么、什么算完成")

    state = load_state(cfg)
    if actor != "coordinator":
        parent_worker = state.workers.get(actor)
        if parent_worker is None or parent_worker.state == "killed":
            raise PermissionError(f"发起者 {actor!r} 不是存活的蜂群成员，无法召唤帮手")

    slice_tasks = [t.strip() for t in (tasks or []) if str(t).strip()]
    if slice_tasks and n and n != 1:
        raise ValueError("参数 tasks（切片）和 n（同构副本数）不能同时指定，请二选一")
    if n < 1 or n > 16:
        raise ValueError("同构副本数 n 必须在 1..16 之间")

    # 规划本次要召唤的所有 (工蜂名, 对应专属 brief)
    plan: list[tuple[str, str]] = []
    if slice_tasks:
        for idx, slice_target in enumerate(slice_tasks, start=1):
            wname = validate_worker_name(f"{name}-{idx}")
            full_brief = f"{cleaned_brief}\n\n【本工蜂负责切片】{slice_target}"
            plan.append((wname, full_brief))
    elif n > 1:
        for idx in range(1, n + 1):
            wname = validate_worker_name(f"{name}-{idx}")
            plan.append((wname, cleaned_brief))
    else:
        wname = validate_worker_name(name)
        plan.append((wname, cleaned_brief))

    # 检查重名冲突（已存在且未 killed 不允许覆盖；即使 killed 也不鼓励复用同一旧名以免历史混淆）
    for wname, _ in plan:
        if wname in state.workers:
            existing = state.workers[wname]
            raise ValueError(
                f"工蜂名 {wname!r} 已存在（当前状态: {existing.state}），请换一个名字"
            )

    # 解析 fork 记忆来源
    fork_history: list[dict[str, Any]] = []
    fork_source = (fork or "").strip()
    if fork_source:
        if fork_source == "coordinator":
            recent = read_messages(cfg, tail=20)
            fork_history = [
                {"ts": m.ts, "role": "assistant" if m.author == "coordinator" else "user",
                 "author": m.author, "content": m.body}
                for m in recent
            ]
        elif fork_source in state.workers:
            fork_history = [dict(item) for item in state.workers[fork_source].history]
        else:
            raise KeyError(f"无法 fork：找不到成员 {fork_source!r}")

    ws_mode = validate_workspace_mode(workspace or cfg.swarm_workspace_mode or "inherit")
    resolved_adapter, resolved_model = _resolve_model_and_adapter(cfg, state, model, adapter)

    created: list[dict[str, Any]] = []
    wake_targets: list[tuple[str, str, str]] = []
    brief_messages: list[dict[str, Any]] = []

    for wname, wbrief in plan:
        ws_info = prepare_workspace(cfg, wname, ws_mode)
        worker = SwarmWorker(
            name=wname,
            brief=wbrief,
            adapter=resolved_adapter,
            model=resolved_model,
            state="waiting",
            workspace_mode=ws_info["mode"],
            workspace_path=ws_info["path"],
            branch_name=ws_info["branch"],
            fallback_copy=bool(ws_info["fallback_copy"]),
            parent=actor,
            forked_from=fork_source,
            history=[dict(item) for item in fork_history],
        )
        state.workers[wname] = worker
        created.append({**worker.summary_view(), "workspace_detail": ws_info["detail"]})

    save_state(cfg, state)

    # 按蜂群规范：在公开消息板上以 @工蜂名 发布任务简报（全员可见）
    for wname, wbrief in plan:
        post_body = f"@{wname} {wbrief}"
        if actor != "coordinator":
            post_body += f"\n（由 @{actor} 召唤协助，完成后请直接向 @{actor} 汇报）"
        msg = append_message(
            cfg,
            author=actor,
            body=post_body,
            kind="brief",
            mentions=[wname],
        )
        brief_messages.append(msg.to_dict())
        record_ledger(
            cfg,
            "swarm_add",
            worker=wname,
            actor=actor,
            adapter=resolved_adapter,
            model=resolved_model,
            workspace_mode=ws_mode,
            forked_from=fork_source,
        )
        if auto_wake:
            wake_targets.append((wname, actor, post_body))

    replies = _wake_workers_parallel(cfg, wake_targets) if wake_targets else []

    return {
        "ok": True,
        "added": created,
        "brief_messages": brief_messages,
        "replies": replies,
    }


def post_message(
    cfg: TowerConfig,
    body: str,
    *,
    author: str = "coordinator",
    kind: str = "post",
    auto_wake: bool = True,
    max_hops: int = 2,
) -> dict[str, Any]:
    """往公开共享消息板发消息，并自动唤醒被 @提及 的工蜂（支持 @all 与自动复活 stopped）。"""
    msg = append_message(cfg, author=author, body=body, kind=kind)
    if not auto_wake:
        return {"ok": True, "message": msg.to_dict(), "woken": [], "skipped": [], "replies": []}

    all_replies: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    woken_names: list[str] = []

    frontier: list[tuple[str, str, list[str]]] = [(author, msg.body, list(msg.mentions))]
    hops = 0

    while frontier and hops < max(1, max_hops):
        state = load_state(cfg)
        next_targets: list[tuple[str, str, str]] = []
        seen_this_hop: set[str] = set()

        for sender, text, mentions in frontier:
            resolved_names: list[str] = []
            for token in mentions:
                if token == "all":
                    for wname, worker in state.workers.items():
                        if wname != sender and worker.state != "killed":
                            resolved_names.append(wname)
                elif token == "coordinator":
                    continue
                elif token in state.workers:
                    if token != sender:
                        resolved_names.append(token)
                else:
                    skipped.append({"target": token, "reason": "未知工蜂名"})

            for target_name in resolved_names:
                worker = state.workers[target_name]
                if worker.state == "killed":
                    skipped.append({
                        "target": target_name,
                        "reason": "该工蜂已永久解散（killed），不能再唤醒，请重新 add",
                    })
                    continue
                if target_name in seen_this_hop:
                    continue
                seen_this_hop.add(target_name)
                next_targets.append((target_name, sender, text))

        if not next_targets:
            break

        hop_replies = _wake_workers_parallel(cfg, next_targets)
        all_replies.extend(hop_replies)
        woken_names.extend(r["worker"] for r in hop_replies)

        # 如果工蜂回执里又 @了别的工蜂，进入下一跳（受 max_hops 封顶，防止死循环）
        frontier = [
            (r["worker"], r["message"]["body"], r["mentions"])
            for r in hop_replies
            if r.get("mentions")
        ]
        hops += 1

    return {
        "ok": True,
        "message": msg.to_dict(),
        "woken": woken_names,
        "skipped": skipped,
        "replies": all_replies,
    }


def switch_model(
    cfg: TowerConfig,
    target_name: str,
    new_model: str,
    *,
    reason: str = "",
    actor: str = "coordinator",
    auto_resume: bool = True,
) -> dict[str, Any]:
    """原地为工蜂（或总调度默认配置）切换模型，完整保留历史记忆与工位。"""
    cleaned_model = (new_model or "").strip()
    if not cleaned_model:
        raise ValueError("新模型名称不能为空")

    state = load_state(cfg)
    target = (target_name or "").strip()

    # 切换总调度默认模型
    if not target or target == "coordinator":
        if actor != "coordinator":
            raise PermissionError("只有总调度（coordinator）可以切换自己的默认模型")
        old_model = state.default_model
        new_adapter, resolved_model = _resolve_model_and_adapter(cfg, state, cleaned_model)
        state.default_adapter = new_adapter
        state.default_model = resolved_model
        save_state(cfg, state)
        note = f"总调度默认模型已从 `{old_model or '(default)'}` 切换为 `{resolved_model}`（后端 `{new_adapter}`）"
        if reason:
            note += f"，原因：{reason}"
        msg = append_message(cfg, author="coordinator", body=note, kind="model_switch")
        record_ledger(cfg, "swarm_model_switch", target="coordinator", old=old_model, new=resolved_model, reason=reason)
        return {
            "ok": True,
            "target": "coordinator",
            "old_model": old_model,
            "new_model": resolved_model,
            "adapter": new_adapter,
            "message": msg.to_dict(),
            "reply": None,
        }

    worker = state.workers.get(target)
    if worker is None:
        raise KeyError(f"找不到工蜂 {target!r}")

    # 权限校验：coordinator 可切任意工蜂；工蜂只能切自己或自己召唤的帮手
    if actor != "coordinator" and actor != target and worker.parent != actor:
        raise PermissionError(
            f"无权切换工蜂 {target!r} 的模型：你（{actor}）只能切换自己或自己召唤的帮手"
        )
    if worker.state == "killed":
        raise ValueError(f"工蜂 {target!r} 已解散（killed），无法切换模型")
    if worker.state == "working":
        raise ValueError(f"工蜂 {target!r} 正在干活（working），请等它回到 waiting 休眠态后再切换模型")

    old_model = worker.model
    old_adapter = worker.adapter
    new_adapter, resolved_model = _resolve_model_and_adapter(cfg, state, cleaned_model)
    # 若用户只写了模型名且没显式改适配器，优先保持能匹配的适配器或沿用原适配器
    if ":" not in cleaned_model and new_adapter == state.default_adapter and old_adapter != state.default_adapter:
        new_adapter = old_adapter

    worker.model = resolved_model
    worker.adapter = new_adapter
    worker.updated_at = now_ts()
    save_state(cfg, state)

    notice = (
        f"@{target} 已为你无损切换大脑：`{old_model or '(default)'}` → `{resolved_model}`"
        f"（保留全部 {len(worker.history)} 条历史记忆与当前工位）。"
    )
    if reason:
        notice += f" 原因：{reason}。"
    notice += " 请基于既有进度继续完成未尽事项。"
    msg = append_message(cfg, author=actor, body=notice, kind="model_switch", mentions=[target])
    record_ledger(
        cfg,
        "swarm_model_switch",
        target=target,
        actor=actor,
        old=old_model,
        new=resolved_model,
        preserved_turns=len(worker.history),
        reason=reason,
    )

    reply = None
    if auto_resume:
        reply = _execute_worker_turn(cfg, target, actor, notice, is_resume=True)

    return {
        "ok": True,
        "target": target,
        "old_model": old_model,
        "new_model": resolved_model,
        "adapter": new_adapter,
        "preserved_turns": len(worker.history),
        "message": msg.to_dict(),
        "reply": reply,
    }


def verify_worker(
    cfg: TowerConfig,
    name: str,
    *,
    land: bool = False,
    no_review: bool = False,
) -> dict[str, Any]:
    """混合增强核心：用控制塔铁面质检门核查工蜂成果（真文件 + 真跑 verify_commands），通过可自动合并工位。"""
    state = load_state(cfg)
    worker = state.workers.get(name)
    if worker is None:
        raise KeyError(f"找不到工蜂 {name!r}")
    if worker.state == "killed":
        raise ValueError(f"工蜂 {name!r} 已解散（killed），无法验收")

    ws = Path(worker.workspace_path or cfg.workspace).resolve()
    problems: list[str] = []
    ran: list[dict[str, Any]] = []

    # 1) 检查工蜂声明的文件是否真实存在于它的工位中
    checked_files: list[str] = []
    for raw_path in worker.last_files:
        candidate = Path(raw_path)
        full = candidate if candidate.is_absolute() else (ws / candidate)
        if not full.exists():
            # 也检查主工作区以防 inherit 路径写法
            alt = Path(cfg.workspace) / candidate
            if not alt.exists():
                problems.append(f"报告里声明的文件不存在：{raw_path}")
                continue
            full = alt
        checked_files.append(str(full))

    # 2) 在工蜂自己的工位里实际执行 tower.toml 配置的 verify_commands
    for cmd in cfg.verify_commands or []:
        t0 = time.time()
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                cwd=str(ws),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300,
            )
            elapsed = round(time.time() - t0, 2)
            out_tail = ((proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")).strip()[-600:]
            ran.append({
                "command": cmd,
                "exit_code": proc.returncode,
                "seconds": elapsed,
                "output": out_tail,
            })
            if proc.returncode != 0:
                problems.append(f"验证命令失败 `{cmd}` (exit {proc.returncode}): {out_tail[:200]}")
        except subprocess.TimeoutExpired:
            ran.append({"command": cmd, "exit_code": 124, "seconds": 300.0, "output": "timeout"})
            problems.append(f"验证命令超时 `{cmd}`")

    ok = len(problems) == 0
    merge_result = None

    if ok and land and worker.workspace_mode != "inherit":
        merge_result = merge_workspace(cfg, worker)
        if not merge_result["ok"]:
            ok = False
            problems.append(merge_result["detail"])
        elif merge_result["merged"]:
            worker.merged = True

    worker.verified = ok
    worker.verify_note = (
        "质检通过" + ("并已合并工位" if (merge_result and merge_result.get("merged")) else "")
        if ok
        else "; ".join(problems)
    )
    worker.updated_at = now_ts()
    save_state(cfg, state)

    if ok:
        summary = (
            f"质检通过（{name}）：检查 {len(checked_files)} 个交付文件、跑完 {len(ran)} 条验证命令全部绿灯。"
        )
        if merge_result and merge_result.get("merged"):
            summary += f" {merge_result['detail']}。"
        if no_review:
            summary += "（已显式豁免 QA 二审）"
        msg = append_message(cfg, author="coordinator", body=summary, kind="verify")
    else:
        detail = "\n".join(f"- {p}" for p in problems)
        summary = f"@{name} 控制塔质检未通过，请根据以下真实证据修复后重新汇报：\n{detail}"
        msg = append_message(cfg, author="coordinator", body=summary, kind="verify", mentions=[name])

    record_ledger(
        cfg,
        "swarm_verify",
        worker=name,
        ok=ok,
        problems=len(problems),
        merged=bool(merge_result and merge_result.get("merged")),
    )
    return {
        "ok": ok,
        "worker": name,
        "checked_files": checked_files,
        "ran": ran,
        "problems": problems,
        "merge": merge_result,
        "message": msg.to_dict(),
    }


def merge_worker(cfg: TowerConfig, name: str) -> dict[str, Any]:
    """手动将指定工蜂的独立工位改动合并回主工作区。"""
    state = load_state(cfg)
    worker = state.workers.get(name)
    if worker is None:
        raise KeyError(f"找不到工蜂 {name!r}")
    if worker.state == "killed":
        raise ValueError(f"工蜂 {name!r} 已解散，工位已清理")

    res = merge_workspace(cfg, worker)
    if res["ok"] and res["merged"]:
        worker.merged = True
        worker.updated_at = now_ts()
        save_state(cfg, state)
    msg = append_message(
        cfg,
        author="coordinator",
        body=f"工位合并（{name}）：{res['detail']}",
        kind="merge",
    )
    record_ledger(cfg, "swarm_merge", worker=name, ok=res["ok"], merged=res["merged"], files=len(res["files"]))
    return {**res, "worker": name, "message": msg.to_dict()}


def kill_worker(
    cfg: TowerConfig,
    name: str,
    *,
    actor: str = "coordinator",
    land: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """永久解散指定工蜂并清理其独立工位（不会连带杀死它自己召唤的帮手）。"""
    state = load_state(cfg)
    worker = state.workers.get(name)
    if worker is None:
        raise KeyError(f"找不到工蜂 {name!r}")
    if actor != "coordinator" and worker.parent != actor:
        raise PermissionError(
            f"无权解散工蜂 {name!r}：只有总调度或它的召唤者（{worker.parent}）可以解散它"
        )
    if worker.state == "killed":
        return {"ok": True, "worker": name, "already_killed": True, "detail": "该工蜂此前已解散"}

    merge_res = None
    unmerged_files = changed_files_in_workspace(cfg, worker)
    if land and worker.workspace_mode != "inherit":
        merge_res = merge_workspace(cfg, worker)
        if not merge_res["ok"]:
            raise RuntimeError(f"解散前合并失败，已中止解散以防丢代码：{merge_res['detail']}")
        worker.merged = bool(merge_res.get("merged")) or worker.merged
    elif unmerged_files and not worker.merged and not force:
        raise ValueError(
            f"工蜂 {name!r} 的独立工位还有 {len(unmerged_files)} 个未合并文件"
            f"（如 {unmerged_files[0]}）。请先执行 `tower swarm merge {name}` / `--land`，或加 `--force` 强制丢弃。"
        )

    cleanup = cleanup_workspace(cfg, worker)
    now = now_ts()
    worker.state = "killed"
    worker.killed_at = now
    worker.updated_at = now
    save_state(cfg, state)

    note = f"工蜂 `{name}` 已永久解散（killed），{cleanup['detail']}。"
    if merge_res and merge_res.get("merged"):
        note = f"已先合并 {len(merge_res['files'])} 个文件；" + note
    msg = append_message(cfg, author=actor, body=note, kind="system")
    record_ledger(cfg, "swarm_kill", worker=name, actor=actor, cleaned=cleanup["cleaned"])

    return {
        "ok": True,
        "worker": name,
        "merge": merge_res,
        "cleanup": cleanup,
        "message": msg.to_dict(),
    }


def monitor_check(
    cfg: TowerConfig,
    *,
    stale_seconds: int = 300,
    nudge: bool = False,
) -> dict[str, Any]:
    """蜂群看门狗（Monitor）：排查超时卡死工蜂与无人响应的 @提及，可选自动催办。"""
    state = load_state(cfg)
    messages = read_messages(cfg, tail=50)

    stalled: list[dict[str, Any]] = []
    unverified_ready: list[str] = []
    unanswered: list[dict[str, Any]] = []

    for w in state.workers.values():
        if w.state == "working":
            elapsed = age_s(w.last_active_at) or 0
            if elapsed >= stale_seconds:
                stalled.append({"worker": w.name, "seconds": elapsed})
        elif w.state == "waiting" and w.last_summary and w.verified is None:
            unverified_ready.append(w.name)

    # 找出最后一条含 @提及 的消息之后，被 @ 的工蜂是否已经回复
    for idx, msg in enumerate(messages):
        if not msg.mentions or msg.kind in ("system", "monitor"):
            continue
        later_authors = {m.author for m in messages[idx + 1 :]}
        for target in msg.mentions:
            if target == "all":
                continue
            worker = state.workers.get(target)
            if worker and worker.state in ("waiting", "stopped") and target not in later_authors:
                unanswered.append({"message_id": msg.id, "worker": target, "from": msg.author})

    nudged: list[dict[str, Any]] = []
    if nudge:
        # 优先催办未回复的 @提及，其次把卡死超时工蜂复位为 stopped 并通知
        for item in unanswered[:3]:
            target = item["worker"]
            res = post_message(
                cfg,
                f"@{target} [巡检催办] 你在消息 {item['message_id']} 中被提及但尚未回复，请汇报进展。",
                author="monitor",
                kind="monitor",
                auto_wake=True,
                max_hops=1,
            )
            nudged.append({"worker": target, "reason": f"unanswered {item['message_id']}", "woken": res["woken"]})
        if stalled:
            latest = load_state(cfg)
            for item in stalled:
                w = latest.workers.get(item["worker"])
                if w and w.state == "working":
                    w.state = "stopped"
                    w.updated_at = now_ts()
            save_state(cfg, latest)
            for item in stalled:
                append_message(
                    cfg,
                    author="monitor",
                    body=f"[巡检告警] 工蜂 @{item['worker']} 在 working 状态滞留 {item['seconds']}s，已标记为 stopped，随时可 @唤醒 恢复。",
                    kind="monitor",
                    mentions=[item["worker"]],
                )

    healthy = not stalled and not unanswered
    return {
        "ok": True,
        "healthy": healthy,
        "stalled": stalled,
        "unanswered": unanswered,
        "unverified_ready": unverified_ready,
        "nudged": nudged,
    }


def get_status(cfg: TowerConfig, *, tail: int = 30) -> dict[str, Any]:
    """返回蜂群全局快照（供 CLI、网页看板 /swarm 与监控使用）。"""
    state = load_state(cfg)
    messages = read_messages(cfg, tail=tail)
    total_messages = len(read_messages(cfg, tail=None))
    counts = {"working": 0, "waiting": 0, "stopped": 0, "killed": 0}
    workers_view: list[dict[str, Any]] = []
    for worker in state.workers.values():
        if worker.state in counts:
            counts[worker.state] += 1
        view = worker.summary_view()
        if worker.state != "killed" and worker.workspace_mode != "inherit":
            view["unmerged_files"] = changed_files_in_workspace(cfg, worker)
        else:
            view["unmerged_files"] = []
        workers_view.append(view)

    # 排序：working 最前 -> waiting -> stopped -> killed
    order = {"working": 0, "waiting": 1, "stopped": 2, "killed": 3}
    workers_view.sort(key=lambda item: (order.get(item["state"], 9), item["name"]))

    health = monitor_check(cfg, nudge=False)
    return {
        "title": state.title or f"{cfg.name} 蜂群",
        "workspace": str(cfg.workspace),
        "default_adapter": state.default_adapter,
        "default_model": state.default_model or "(default)",
        "counts": {**counts, "total_workers": len(workers_view), "messages": total_messages},
        "workers": workers_view,
        "board": [m.to_dict() for m in messages],
        "health": {
            "healthy": health["healthy"],
            "stalled": health["stalled"],
            "unanswered": health["unanswered"],
            "unverified_ready": health["unverified_ready"],
        },
        "updated_at": state.updated_at,
    }


def run_demo(cfg: TowerConfig) -> dict[str, Any]:
    """离线端到端蜂群演示：无需网络/模型，1 秒跑完招人分片→@唤醒→无损换模型→质检验收→合并解散。"""
    steps: list[str] = []
    set_title(cfg, "离线蜂群混合模式演示")
    steps.append("[1] 设定蜂群标题：离线蜂群混合模式演示")

    added = add_workers(
        cfg,
        "coder",
        "完成模块交付并写出验证报告",
        adapter="mock",
        model="mock-default",
        workspace="share",
        tasks=["登录模块", "支付模块"],
        auto_wake=True,
    )
    names = [w["name"] for w in added["added"]]
    steps.append(f"[2] 切片并行召唤 {len(names)} 只工蜂（独立工位 share）：{', '.join(names)}")

    switched = switch_model(
        cfg,
        names[0],
        "mock-pro",
        reason="演示无损切换更强模型并保留历史记忆",
        auto_resume=True,
    )
    steps.append(
        f"[3] 为 {names[0]} 原地换脑：{switched['old_model']} -> {switched['new_model']}（保留 {switched['preserved_turns']} 条记忆）"
    )

    posted = post_message(cfg, "@all 请做最后自检，准备进入控制塔质检门")
    steps.append(f"[4] 公开大群 @all 广播，唤醒 {len(posted['woken'])} 只工蜂并行回复")

    verified = verify_worker(cfg, names[0], land=True, no_review=True)
    steps.append(
        f"[5] 控制塔质检门验收 {names[0]}：{'PASS' if verified['ok'] else 'FAIL'}（已校验 {len(verified['checked_files'])} 个真实文件）"
    )

    killed = kill_worker(cfg, names[0], force=True)
    steps.append(f"[6] 解散 {names[0]} 并清理临时工位：{killed['cleanup']['detail']}")

    snap = get_status(cfg)
    steps.append(
        f"[7] 当前大群消息总数：{snap['counts']['messages']}，存活工蜂：{snap['counts']['waiting'] + snap['counts']['working']}"
    )
    return {"ok": True, "steps": steps, "status": snap}
