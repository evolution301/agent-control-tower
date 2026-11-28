"""`tower swarm` 命令行控制器与参数解析器（Controller）。

将蜂群全部子命令独立封装在此模块，避免主 `cli.py` 过度膨胀，保持单一职责。
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Callable

from ..config import TowerConfig, ensure_layout, parse_config
from .service import (
    add_workers,
    get_status,
    kill_worker,
    list_models,
    merge_worker,
    monitor_check,
    post_message,
    run_demo,
    set_title,
    switch_model,
    verify_worker,
)

_STATE_ICON = {
    "working": "● working",
    "waiting": "○ waiting",
    "stopped": "◐ stopped",
    "killed": "✕ killed",
}


def _print_json(data: object) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def register_swarm_parser(sub: argparse._SubParsersAction, load_cfg: Callable[[argparse.Namespace], TowerConfig]) -> None:
    """向主 CLI 注册 `tower swarm` 及其全部子命令。"""
    sp = sub.add_parser(
        "swarm",
        help="蜂群协作模式：共享消息板 @唤醒、动态招人分片、独立工位隔离、无损换模型、联动质检验收",
    )
    ss = sp.add_subparsers(dest="swarm_command", required=True)

    # 1. status
    s = ss.add_parser("status", help="查看蜂群状态、工蜂花名册与最近群聊")
    s.add_argument("--tail", type=int, default=12, help="附带显示最近 N 条群聊消息（默认 12）")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=lambda a: _cmd_status(a, load_cfg))

    # 2. title
    s = ss.add_parser("title", help="设置蜂群标题（仅总调度）")
    s.add_argument("title", help="蜂群显示标题")
    s.add_argument("--by", default="coordinator", help="操作者身份（默认 coordinator）")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=lambda a: _cmd_title(a, load_cfg))

    # 3. models
    s = ss.add_parser("models", help="列出可选模型目录")
    s.add_argument("--all", dest="all_models", action="store_true", help="显示全部模型（含非推荐项）")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=lambda a: _cmd_models(a, load_cfg))

    # 4. add
    s = ss.add_parser("add", help="召唤 1 只或一批工蜂并公开派发任务说明")
    s.add_argument("name", help="工蜂名（小写字母/数字开头，如 coder、qa）")
    s.add_argument("--brief", required=True, help="明确分工与完成标准（公开显示在共享消息板）")
    s.add_argument("--model", default="", help="指定模型（支持 adapter:model 写法，如 openai:deepseek-chat）")
    s.add_argument("--adapter", default="", help="指定后端适配器：mock | cli | openai | dsh")
    s.add_argument("--workspace", default="", choices=["", "inherit", "share", "branch"],
                   help="工位隔离模式：inherit(共享) | share(git worktree) | branch(独立克隆)")
    s.add_argument("--fork", default="", help="克隆某位成员或 coordinator 的对话记忆")
    s.add_argument("--n", type=int, default=1, help="召唤 N 只同构工蜂（<name>-1 .. <name>-N）")
    s.add_argument("--tasks", nargs="+", default=None, help="按切片清单并行召唤（<name>-1 .. <name>-K）")
    s.add_argument("--by", default="coordinator", help="发起者（coordinator 或某只存活工蜂名）")
    s.add_argument("--no-wake", action="store_true", help="仅登记并发布 brief，先不立即唤醒执行")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=lambda a: _cmd_add(a, load_cfg))

    # 5. post
    s = ss.add_parser("post", help="在共享消息板发消息，并自动 @唤醒 对应工蜂（支持 @all）")
    s.add_argument("message", help="消息内容，使用 @工蜂名 或 @all 唤醒对应成员")
    s.add_argument("--by", default="coordinator", help="发言人（默认 coordinator）")
    s.add_argument("--no-wake", action="store_true", help="只发消息不触发唤醒")
    s.add_argument("--max-hops", type=int, default=2, help="工蜂之间级联 @唤醒 最大跳数（默认 2）")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=lambda a: _cmd_post(a, load_cfg))

    # 6. board
    s = ss.add_parser("board", help="阅读共享消息板记录")
    s.add_argument("--tail", type=int, default=30, help="显示最近 N 条消息（默认 30）")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=lambda a: _cmd_board(a, load_cfg))

    # 7. model
    s = ss.add_parser("model", help="无损切换工蜂模型（保留全部历史记忆与工位）")
    s.add_argument("name", help="工蜂名（或 coordinator 切换默认模型）")
    s.add_argument("new_model", help="新模型名（支持 adapter:model）")
    s.add_argument("--reason", default="", help="切换原因（记录在公开消息板）")
    s.add_argument("--by", default="coordinator", help="操作者身份")
    s.add_argument("--no-resume", action="store_true", help="只切换配置，不立即唤醒续跑")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=lambda a: _cmd_model(a, load_cfg))

    # 8. verify
    s = ss.add_parser("verify", help="用控制塔质检门核查工蜂成果（真文件 + 真跑 verify_commands）")
    s.add_argument("name", help="工蜂名")
    s.add_argument("--land", action="store_true", help="验证通过后自动合并该工蜂的独立工位")
    s.add_argument("--no-review", action="store_true", help="显式记录跳过 QA 二审")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=lambda a: _cmd_verify(a, load_cfg))

    # 9. merge
    s = ss.add_parser("merge", help="将工蜂的独立工位（share/branch）改动合并回主工作区")
    s.add_argument("name", help="工蜂名")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=lambda a: _cmd_merge(a, load_cfg))

    # 10. kill
    s = ss.add_parser("kill", help="永久解散工蜂并自动清理它的独立工位")
    s.add_argument("name", help="工蜂名")
    s.add_argument("--by", default="coordinator", help="操作者身份")
    s.add_argument("--land", action="store_true", help="解散前先合并其独立工位改动")
    s.add_argument("--force", action="store_true", help="强行解散（即使有未合并改动）")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=lambda a: _cmd_kill(a, load_cfg))

    # 11. monitor
    s = ss.add_parser("monitor", help="巡检蜂群健康状况（排查卡死工蜂与无人响应的 @提及）")
    s.add_argument("--stale", type=int, default=300, help="working 超过多少秒视为卡死（默认 300）")
    s.add_argument("--nudge", action="store_true", help="自动在消息板 @催办 未响应成员并复位卡死工蜂")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=lambda a: _cmd_monitor(a, load_cfg))

    # 12. demo
    s = ss.add_parser("demo", help="离线 1 秒端到端蜂群演示（无需模型、无需网络）")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=lambda a: _cmd_demo(a, load_cfg))


def _emit_workspace(cfg: TowerConfig) -> None:
    print(f"resolved workspace = {cfg.workspace}", file=sys.stderr)


def _cmd_status(args: argparse.Namespace, load_cfg: Callable[[argparse.Namespace], TowerConfig]) -> int:
    cfg = load_cfg(args)
    snap = get_status(cfg, tail=args.tail)
    if args.json:
        _print_json(snap)
        return 0
    c = snap["counts"]
    print(f"🐝 {snap['title']}  (default: {snap['default_adapter']} / {snap['default_model']})")
    print(
        f"   working={c['working']}  waiting={c['waiting']}  "
        f"stopped={c['stopped']}  killed={c['killed']}  messages={c['messages']}"
    )
    if not snap["workers"]:
        print("   （还没有工蜂，试试：tower swarm add coder --brief \"...\" --workspace share）")
    else:
        print("\n工蜂花名册:")
        for w in snap["workers"]:
            lamp = _STATE_ICON.get(w["state"], w["state"])
            vtag = ""
            if w["verified"] is True:
                vtag = " [✓ verified]"
            elif w["verified"] is False:
                vtag = " [✗ verify-fail]"
            unmerged = len(w.get("unmerged_files") or [])
            utag = f" ({unmerged} unmerged)" if unmerged else ""
            print(
                f"  - {w['name']:<16} {lamp:<11} {w['adapter']}:{w['model']:<15} "
                f"ws={w['workspace_mode']}{utag}{vtag}"
            )
    if snap["board"]:
        print(f"\n最近共享消息板（{len(snap['board'])} 条）:")
        for m in snap["board"][-6:]:
            first = m["body"].splitlines()[0][:90]
            print(f"  [{m['id']}] {m['author']} ({m['kind']}): {first}")
    return 0


def _cmd_title(args: argparse.Namespace, load_cfg: Callable[[argparse.Namespace], TowerConfig]) -> int:
    cfg = load_cfg(args)
    _emit_workspace(cfg)
    try:
        res = set_title(cfg, args.title, actor=args.by)
    except (ValueError, PermissionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        _print_json(res)
    else:
        print(f"已更新蜂群标题：{res['title']}")
    return 0


def _cmd_models(args: argparse.Namespace, load_cfg: Callable[[argparse.Namespace], TowerConfig]) -> int:
    cfg = load_cfg(args)
    res = list_models(cfg, all_models=args.all_models)
    if args.json:
        _print_json(res)
        return 0
    print(f"默认后端: {res['default_adapter']}  默认模型: {res['default_model'] or '(default)'}")
    for m in res["models"]:
        star = "★" if m.get("suggested") else " "
        print(f"  {star} {m['id']:<20} ({m['adapter']:<6})  {m['label']}")
    return 0


def _cmd_add(args: argparse.Namespace, load_cfg: Callable[[argparse.Namespace], TowerConfig]) -> int:
    cfg = load_cfg(args)
    _emit_workspace(cfg)
    try:
        res = add_workers(
            cfg,
            args.name,
            args.brief,
            model=args.model,
            adapter=args.adapter,
            workspace=args.workspace,
            fork=args.fork,
            n=args.n,
            tasks=args.tasks,
            actor=args.by,
            auto_wake=not args.no_wake,
        )
    except (ValueError, KeyError, PermissionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        _print_json(res)
        return 0
    for w in res["added"]:
        print(f"+ 已召唤工蜂 {w['name']} ({w['adapter']}:{w['model']}, ws={w['workspace_mode']}) — {w['workspace_detail']}")
    for r in res["replies"]:
        first = r["message"]["body"].splitlines()[0][:100]
        print(f"  ↳ [{r['message']['id']}] {r['worker']}: {first}")
    return 0


def _cmd_post(args: argparse.Namespace, load_cfg: Callable[[argparse.Namespace], TowerConfig]) -> int:
    cfg = load_cfg(args)
    _emit_workspace(cfg)
    try:
        res = post_message(
            cfg,
            args.message,
            author=args.by,
            auto_wake=not args.no_wake,
            max_hops=args.max_hops,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        _print_json(res)
        return 0
    msg = res["message"]
    print(f"[{msg['id']}] {msg['author']}: {msg['body']}")
    for r in res["replies"]:
        first = r["message"]["body"].splitlines()[0][:100]
        print(f"  ↳ [{r['message']['id']}] {r['worker']}: {first}")
    for s in res["skipped"]:
        print(f"  ! 跳过 @{s['target']}: {s['reason']}", file=sys.stderr)
    return 0


def _cmd_board(args: argparse.Namespace, load_cfg: Callable[[argparse.Namespace], TowerConfig]) -> int:
    cfg = load_cfg(args)
    snap = get_status(cfg, tail=args.tail)
    if args.json:
        _print_json({"title": snap["title"], "messages": snap["board"]})
        return 0
    if not snap["board"]:
        print("（共享消息板为空）")
        return 0
    for m in snap["board"]:
        print(f"[{m['id']}] {m['ts']} {m['author']} ({m['kind']}):")
        for line in m["body"].splitlines():
            print(f"    {line}")
    return 0


def _cmd_model(args: argparse.Namespace, load_cfg: Callable[[argparse.Namespace], TowerConfig]) -> int:
    cfg = load_cfg(args)
    _emit_workspace(cfg)
    try:
        res = switch_model(
            cfg,
            args.name,
            args.new_model,
            reason=args.reason,
            actor=args.by,
            auto_resume=not args.no_resume,
        )
    except (ValueError, KeyError, PermissionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        _print_json(res)
        return 0
    print(f"已为 {res['target']} 切换模型：{res['old_model'] or '(default)'} -> {res['new_model']}（后端 {res['adapter']}）")
    if res.get("reply"):
        r = res["reply"]
        print(f"  ↳ 续跑回执 [{r['message']['id']}]: {r['message']['body'].splitlines()[0][:100]}")
    return 0


def _cmd_verify(args: argparse.Namespace, load_cfg: Callable[[argparse.Namespace], TowerConfig]) -> int:
    cfg = load_cfg(args)
    _emit_workspace(cfg)
    try:
        res = verify_worker(cfg, args.name, land=args.land, no_review=args.no_review)
    except (ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        _print_json(res)
        return 0 if res["ok"] else 1
    verdict = "PASS" if res["ok"] else "FAIL"
    print(f"verify {res['worker']}: {verdict} (files={len(res['checked_files'])}, commands={len(res['ran'])})")
    for p in res["problems"]:
        print(f"  - {p}")
    if res.get("merge") and res["merge"].get("merged"):
        print(f"  ↳ {res['merge']['detail']}")
    return 0 if res["ok"] else 1


def _cmd_merge(args: argparse.Namespace, load_cfg: Callable[[argparse.Namespace], TowerConfig]) -> int:
    cfg = load_cfg(args)
    _emit_workspace(cfg)
    try:
        res = merge_worker(cfg, args.name)
    except (ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        _print_json(res)
        return 0 if res["ok"] else 1
    print(res["detail"])
    return 0 if res["ok"] else 1


def _cmd_kill(args: argparse.Namespace, load_cfg: Callable[[argparse.Namespace], TowerConfig]) -> int:
    cfg = load_cfg(args)
    _emit_workspace(cfg)
    try:
        res = kill_worker(cfg, args.name, actor=args.by, land=args.land, force=args.force)
    except (ValueError, KeyError, PermissionError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        _print_json(res)
        return 0
    print(f"已永久解散工蜂 {res['worker']}（{res.get('cleanup', {}).get('detail', res.get('detail', ''))}）")
    return 0


def _cmd_monitor(args: argparse.Namespace, load_cfg: Callable[[argparse.Namespace], TowerConfig]) -> int:
    cfg = load_cfg(args)
    res = monitor_check(cfg, stale_seconds=args.stale, nudge=args.nudge)
    if args.json:
        _print_json(res)
        return 0
    print(f"蜂群巡检：{'健康' if res['healthy'] else '发现需关注项'}")
    if res["stalled"]:
        print(f"  - 超时卡死 working: {res['stalled']}")
    if res["unanswered"]:
        print(f"  - 未回复 @提及: {res['unanswered']}")
    if res["unverified_ready"]:
        print(f"  - 已完成待质检工蜂: {', '.join(res['unverified_ready'])}")
    if res["nudged"]:
        print(f"  - 已自动催办: {[n['worker'] for n in res['nudged']]}")
    return 0


def _cmd_demo(args: argparse.Namespace, load_cfg: Callable[[argparse.Namespace], TowerConfig]) -> int:
    explicit = getattr(args, "global_workspace", None) or getattr(args, "workspace", None)
    if explicit:
        cfg = load_cfg(args)
        res = run_demo(cfg)
    else:
        with tempfile.TemporaryDirectory(prefix="tower-swarm-demo-") as tmp:
            root = Path(tmp)
            cfg_path = root / "tower.json"
            cfg_path.write_text(
                json.dumps({
                    "name": "swarm-demo",
                    "workspace": str(root),
                    "roles": {"coder": {"adapter": "mock", "description": "demo"}},
                }),
                encoding="utf-8",
            )
            cfg = parse_config(cfg_path)
            ensure_layout(cfg)
            res = run_demo(cfg)
    if args.json:
        _print_json(res)
        return 0
    for line in res["steps"]:
        print(line)
    return 0
