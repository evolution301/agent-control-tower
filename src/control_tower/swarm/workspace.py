"""独立工位隔离服务（Workspace Isolation）。

仿照蜂群系统的三种工作区模式：
- inherit：共享主工作区（适合只读调研、代码审查、QA 质检）
- share  ：通过 git worktree 创建共享底层仓库的独立工作目录（多只开发工蜂同时改代码互不踩文件）
- branch ：通过 git local clone 创建完全独立的分支副本（适合高风险大改）

如果当前项目尚未初始化 Git 仓库（或还没有首次提交），share/branch 会自动降级为
安全的隔离目录拷贝，确保零崩溃、开箱即用。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..config import TowerConfig
from .models import SwarmWorker, validate_workspace_mode
from .repository import workspaces_dir

# 非 Git 降级拷贝时跳过的目录，避免循环拷贝 .tower 或大体积依赖目录
_COPY_IGNORE = shutil.ignore_patterns(
    ".git",
    ".tower",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "node_modules",
)


def _run_git(args: list[str], cwd: Path, timeout_s: int = 60) -> subprocess.CompletedProcess[str]:
    """执行 git 子命令并统一使用 UTF-8 捕获输出。"""
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_s,
    )


def has_git_head(root: Path) -> bool:
    """检查目标目录是否是有效的 Git 仓库且至少已有一次提交（HEAD 存在）。"""
    try:
        inside = _run_git(["rev-parse", "--is-inside-work-tree"], root, timeout_s=10)
        if inside.returncode != 0 or inside.stdout.strip().lower() != "true":
            return False
        head = _run_git(["rev-parse", "--verify", "HEAD"], root, timeout_s=10)
        return head.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _copy_workspace(src: Path, dst: Path) -> None:
    """安全复制工作区文件到独立目录（用于非 Git 仓库的降级隔离）。"""
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, ignore=_COPY_IGNORE, dirs_exist_ok=True)


def prepare_workspace(cfg: TowerConfig, worker_name: str, mode: str) -> dict[str, Any]:
    """为工蜂准备对应隔离级别的工作目录，返回路径、分支与说明信息。"""
    resolved_mode = validate_workspace_mode(mode)
    base_root = Path(cfg.project_root or cfg.workspace).resolve()

    if resolved_mode == "inherit":
        return {
            "mode": "inherit",
            "path": str(base_root),
            "branch": "",
            "fallback_copy": False,
            "detail": "共享主工作区（inherit）",
        }

    target_dir = (workspaces_dir(cfg) / worker_name).resolve()
    branch_name = f"swarm/{worker_name}"

    if target_dir.exists():
        shutil.rmtree(target_dir, ignore_errors=True)

    if has_git_head(base_root):
        if resolved_mode == "share":
            # 如果同名旧分支残留，先尝试安全删除再建 worktree
            _run_git(["worktree", "prune"], base_root)
            _run_git(["branch", "-D", branch_name], base_root)
            res = _run_git(
                ["worktree", "add", "-b", branch_name, str(target_dir)],
                base_root,
            )
            if res.returncode == 0:
                return {
                    "mode": "share",
                    "path": str(target_dir),
                    "branch": branch_name,
                    "fallback_copy": False,
                    "detail": f"已创建 git worktree 独立工位（分支 {branch_name}）",
                }
        elif resolved_mode == "branch":
            clone = _run_git(
                ["clone", "--local", "--no-hardlinks", str(base_root), str(target_dir)],
                base_root.parent,
            )
            if clone.returncode == 0:
                checkout = _run_git(["checkout", "-B", branch_name], target_dir)
                if checkout.returncode == 0:
                    return {
                        "mode": "branch",
                        "path": str(target_dir),
                        "branch": branch_name,
                        "fallback_copy": False,
                        "detail": f"已创建独立克隆分支工位（分支 {branch_name}）",
                    }

    # 非 Git 仓库或 git worktree 不可用时，平滑降级为隔离目录拷贝
    _copy_workspace(base_root, target_dir)
    return {
        "mode": resolved_mode,
        "path": str(target_dir),
        "branch": "",
        "fallback_copy": True,
        "detail": f"非 Git 仓库或无法建分支，已自动降级为独立隔离目录拷贝（{resolved_mode}）",
    }


def changed_files_in_workspace(cfg: TowerConfig, worker: SwarmWorker) -> list[str]:
    """检测工蜂工位里新增或修改过的文件列表（相对路径）。"""
    if worker.workspace_mode == "inherit" or not worker.workspace_path:
        return []
    ws = Path(worker.workspace_path)
    if not ws.exists():
        return []

    if not worker.fallback_copy and has_git_head(ws):
        status = _run_git(["status", "--porcelain"], ws)
        files: list[str] = []
        if status.returncode == 0:
            for line in status.stdout.splitlines():
                if len(line) >= 4:
                    files.append(line[3:].strip())
        # 同时检查相对主仓库 HEAD 已提交但尚未合并的改动
        base_root = Path(cfg.project_root or cfg.workspace).resolve()
        if worker.branch_name and has_git_head(base_root):
            diff = _run_git(["diff", "--name-only", f"HEAD...{worker.branch_name}"], base_root)
            if diff.returncode == 0:
                for name in diff.stdout.splitlines():
                    cleaned = name.strip()
                    if cleaned and cleaned not in files:
                        files.append(cleaned)
        return files

    # 降级目录拷贝模式：与主工作区逐文件比对新增或内容变化
    base_root = Path(cfg.project_root or cfg.workspace).resolve()
    changed: list[str] = []
    for path in ws.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(ws)
        if any(part in (".git", ".tower", "__pycache__") for part in rel.parts):
            continue
        origin = base_root / rel
        if not origin.exists():
            changed.append(rel.as_posix())
        else:
            try:
                if path.read_bytes() != origin.read_bytes():
                    changed.append(rel.as_posix())
            except OSError:
                continue
    return sorted(changed)


def merge_workspace(cfg: TowerConfig, worker: SwarmWorker) -> dict[str, Any]:
    """将工蜂独立工位（share/branch）中的改动安全合并回主工作区。"""
    if worker.workspace_mode == "inherit":
        return {
            "ok": True,
            "merged": False,
            "files": [],
            "detail": "inherit 模式本就共享主工作区，无需合并",
        }

    ws = Path(worker.workspace_path)
    base_root = Path(cfg.project_root or cfg.workspace).resolve()
    if not ws.exists():
        return {
            "ok": False,
            "merged": False,
            "files": [],
            "detail": f"工蜂 {worker.name} 的工位目录不存在：{ws}",
        }

    # 1) Git worktree / clone 分支合并路径
    if not worker.fallback_copy and worker.branch_name and has_git_head(ws) and has_git_head(base_root):
        dirty = _run_git(["status", "--porcelain"], ws)
        if dirty.returncode == 0 and dirty.stdout.strip():
            _run_git(["add", "-A"], ws)
            commit = _run_git(
                [
                    "-c", "user.name=TowerSwarm",
                    "-c", "user.email=swarm@control-tower.local",
                    "commit", "-m", f"swarm({worker.name}): land worker changes",
                ],
                ws,
            )
            if commit.returncode != 0:
                return {
                    "ok": False,
                    "merged": False,
                    "files": [],
                    "detail": f"工位提交改动失败：{commit.stderr.strip() or commit.stdout.strip()}",
                }

        # 如果是 branch 独立克隆，先把分支拉回主仓库引用
        if worker.workspace_mode == "branch":
            fetch = _run_git(
                ["fetch", str(ws), f"{worker.branch_name}:refs/heads/{worker.branch_name}"],
                base_root,
            )
            if fetch.returncode != 0:
                return {
                    "ok": False,
                    "merged": False,
                    "files": [],
                    "detail": f"拉取克隆分支失败：{fetch.stderr.strip() or fetch.stdout.strip()}",
                }

        diff_files = [
            line.strip()
            for line in _run_git(
                ["diff", "--name-only", f"HEAD...{worker.branch_name}"], base_root
            ).stdout.splitlines()
            if line.strip()
        ]
        if not diff_files:
            return {
                "ok": True,
                "merged": False,
                "files": [],
                "detail": f"工蜂 {worker.name} 的分支 {worker.branch_name} 没有新改动需要合并",
            }

        merge_res = _run_git(
            [
                "-c", "user.name=TowerSwarm",
                "-c", "user.email=swarm@control-tower.local",
                "merge", "--no-edit", worker.branch_name,
            ],
            base_root,
        )
        if merge_res.returncode != 0:
            _run_git(["merge", "--abort"], base_root)
            return {
                "ok": False,
                "merged": False,
                "files": diff_files,
                "detail": (
                    f"合并分支 {worker.branch_name} 时出现冲突，已自动回滚以保护主分支："
                    f"{merge_res.stderr.strip() or merge_res.stdout.strip()}"
                ),
            }
        return {
            "ok": True,
            "merged": True,
            "files": diff_files,
            "detail": f"已将分支 {worker.branch_name} 合并回主工作区（共 {len(diff_files)} 个文件）",
        }

    # 2) 非 Git 降级拷贝模式：仅回写发生变化的文件
    files = changed_files_in_workspace(cfg, worker)
    for rel in files:
        src = ws / rel
        dst = base_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return {
        "ok": True,
        "merged": bool(files),
        "files": files,
        "detail": (
            f"已将隔离目录中的 {len(files)} 个变更文件同步回主工作区"
            if files
            else f"工蜂 {worker.name} 的隔离目录没有文件改动"
        ),
    }


def cleanup_workspace(cfg: TowerConfig, worker: SwarmWorker) -> dict[str, Any]:
    """解散工蜂时清理其专属工位（永远不会删除 inherit 主工作区）。"""
    if worker.workspace_mode == "inherit" or not worker.workspace_path:
        return {"cleaned": False, "detail": "inherit 模式保留主工作区不清理"}

    ws = Path(worker.workspace_path).resolve()
    base_root = Path(cfg.project_root or cfg.workspace).resolve()
    swarm_ws_root = workspaces_dir(cfg).resolve()

    # 安全防御：确保待删除路径严格位于 .tower/swarm/workspaces/ 下，绝不误删外部目录
    try:
        ws.relative_to(swarm_ws_root)
    except ValueError:
        return {"cleaned": False, "detail": f"安全保护：拒绝清理非蜂群工位路径 {ws}"}

    if not worker.fallback_copy and worker.workspace_mode == "share" and has_git_head(base_root):
        _run_git(["worktree", "remove", "--force", str(ws)], base_root)
        _run_git(["worktree", "prune"], base_root)
        if worker.branch_name:
            _run_git(["branch", "-D", worker.branch_name], base_root)

    if ws.exists():
        shutil.rmtree(ws, ignore_errors=True)

    return {"cleaned": True, "detail": f"已清理独立工位 {ws.name}"}
