"""蜂群混合模式（Swarm + Control Tower）全链路单元测试与集成测试。"""

from __future__ import annotations

import contextlib
import json
import subprocess
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from control_tower import cli, swarm
from control_tower.config import ensure_layout, parse_config
from control_tower.panel import make_handler


def _make_tower(root: Path, *, verify_commands: list[str] | None = None, git_init: bool = False):
    root.mkdir(parents=True, exist_ok=True)
    if git_init:
        subprocess.run(["git", "init"], cwd=str(root), check=True, capture_output=True)
        (root / "README-proj.txt").write_text("hello\n", encoding="utf-8")
        subprocess.run(["git", "add", "README-proj.txt"], cwd=str(root), check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@local", "commit", "-m", "init"],
            cwd=str(root),
            check=True,
            capture_output=True,
        )
    cfg_data = {
        "name": "swarm-test",
        "workspace": str(root),
        "verify_commands": verify_commands or [],
        "roles": {
            "coder": {"description": "dev", "adapter": "mock", "write_scopes": ["src/"]},
            "qa": {"description": "qa", "adapter": "mock", "write_scopes": []},
        },
        "swarm": {
            "default_adapter": "mock",
            "default_model": "mock-default",
            "models": [
                {"id": "custom-brain", "label": "Custom Brain", "adapter": "mock", "suggested": True},
            ],
        },
    }
    cfg_path = root / "tower.json"
    cfg_path.write_text(json.dumps(cfg_data), encoding="utf-8")
    cfg = parse_config(cfg_path)
    ensure_layout(cfg)
    return cfg


def test_name_validation_and_mention_extraction():
    assert swarm.validate_worker_name("coder-1") == "coder-1"
    assert swarm.validate_worker_name("qa_lead") == "qa_lead"
    for bad in ("", "all", "coordinator", "coordinator-1", "UpperCase", "-dash", "a" * 25):
        with pytest.raises(ValueError):
            swarm.validate_worker_name(bad)

    mentions = swarm.extract_mentions("请 @coder-1 和 @qa 看一下，不要匹配 user@example.com，再抄送 @coder-1 和 @all")
    assert mentions == ["coder-1", "qa", "all"]


def test_add_sharded_homogeneous_fork_and_helper_permissions(tmp_path):
    cfg = _make_tower(tmp_path)

    # 1. 按切片并行召唤 2 只工蜂
    res = swarm.add_workers(
        cfg, "coder", "补齐模块测试", tasks=["登录", "支付"], workspace="inherit"
    )
    names = [w["name"] for w in res["added"]]
    assert names == ["coder-1", "coder-2"]
    assert len(res["replies"]) == 2

    # 2. 克隆 coder-1 的历史记忆召唤 reviewer
    forked = swarm.add_workers(
        cfg, "reviewer", "基于历史记忆复核改动", fork="coder-1", auto_wake=False
    )
    state = swarm.load_state(cfg)
    assert state.workers["reviewer"].forked_from == "coder-1"
    assert len(state.workers["reviewer"].history) == len(state.workers["coder-1"].history)
    assert len(state.workers["reviewer"].history) >= 2

    # 3. 工蜂 coder-1 自己召唤帮手 helper-a
    helper = swarm.add_workers(cfg, "helper-a", "帮我整理日志", actor="coder-1")
    assert helper["added"][0]["parent"] == "coder-1"

    # 4. 权限边界：coder-2 不能解散 coder-1 召唤的 helper-a
    with pytest.raises(PermissionError):
        swarm.kill_worker(cfg, "helper-a", actor="coder-2")

    # 5. 解散 coder-1 不会连带杀掉它召唤的 helper-a
    swarm.kill_worker(cfg, "coder-1", actor="coordinator", force=True)
    after = swarm.load_state(cfg)
    assert after.workers["coder-1"].state == "killed"
    assert after.workers["helper-a"].state == "waiting"


def test_post_mentions_revive_stopped_and_skip_killed(tmp_path):
    cfg = _make_tower(tmp_path)
    swarm.add_workers(cfg, "alpha", "任务A", auto_wake=False)
    swarm.add_workers(cfg, "beta", "任务B", auto_wake=False)

    # 模拟重启后 alpha 处于 stopped，beta 已 killed
    state = swarm.load_state(cfg)
    state.workers["alpha"].state = "stopped"
    state.workers["beta"].state = "killed"
    swarm.save_state(cfg, state)

    res = swarm.post_message(cfg, "@alpha @beta 请汇报进度")
    assert res["woken"] == ["alpha"]
    assert any(s["target"] == "beta" for s in res["skipped"])
    assert swarm.load_state(cfg).workers["alpha"].state == "waiting"

    # @all 只唤醒存活的 alpha
    bcast = swarm.post_message(cfg, "@all 全体同步状态")
    assert bcast["woken"] == ["alpha"]


def test_switch_model_preserves_history_and_enforces_state(tmp_path):
    cfg = _make_tower(tmp_path)
    swarm.add_workers(cfg, "dev", "写核心模块", model="mock-default")
    before_turns = len(swarm.load_state(cfg).workers["dev"].history)
    assert before_turns == 2

    switched = swarm.switch_model(cfg, "dev", "mock-pro", reason="额度用尽切更强模型", auto_resume=True)
    assert switched["old_model"] == "mock-default"
    assert switched["new_model"] == "mock-pro"
    assert switched["preserved_turns"] == before_turns
    after_worker = swarm.load_state(cfg).workers["dev"]
    assert after_worker.model == "mock-pro"
    # 原记忆保留 + 续跑新增 2 条
    assert len(after_worker.history) == before_turns + 2
    assert "mock-pro" in switched["reply"]["message"]["body"]

    # working 状态拒绝切换
    state = swarm.load_state(cfg)
    state.workers["dev"].state = "working"
    swarm.save_state(cfg, state)
    with pytest.raises(ValueError):
        swarm.switch_model(cfg, "dev", "custom-brain")


def test_git_worktree_isolation_verify_gate_and_merge_land(tmp_path):
    cfg = _make_tower(
        tmp_path,
        verify_commands=['python -c "import pathlib; assert pathlib.Path(\'swarm-fe-output.md\').exists()"'],
        git_init=True,
    )

    added = swarm.add_workers(cfg, "fe", "开发页面组件", workspace="share", model="mock-default")
    worker_info = added["added"][0]
    assert worker_info["workspace_mode"] == "share"
    assert worker_info["branch_name"] == "swarm/fe"
    assert worker_info["fallback_copy"] is False

    # 隔离验证：合并前主工作区不存在工蜂的产物文件
    assert not (tmp_path / "swarm-fe-output.md").exists()
    assert (Path(worker_info["workspace_path"]) / "swarm-fe-output.md").exists()

    # 未合并前不带 --force / --land 解散会被安全拦住
    with pytest.raises(ValueError):
        swarm.kill_worker(cfg, "fe")

    # 通过控制塔质检门 + --land 自动合并回主工作区
    ver = swarm.verify_worker(cfg, "fe", land=True, no_review=True)
    assert ver["ok"] is True
    assert ver["merge"]["merged"] is True
    assert (tmp_path / "swarm-fe-output.md").exists()

    # 合并后安全解散并清理 worktree
    killed = swarm.kill_worker(cfg, "fe")
    assert killed["cleanup"]["cleaned"] is True
    assert not Path(worker_info["workspace_path"]).exists()
    # 主工作区文件完好无损
    assert (tmp_path / "swarm-fe-output.md").exists()


def test_verify_gate_fails_on_missing_file_or_bad_command(tmp_path):
    cfg = _make_tower(tmp_path, verify_commands=['python -c "import sys; sys.exit(1)"'])
    swarm.add_workers(cfg, "be", "写接口", workspace="inherit")

    ver = swarm.verify_worker(cfg, "be")
    assert ver["ok"] is False
    assert ver["problems"]
    assert swarm.load_state(cfg).workers["be"].verified is False
    # 失败证据应公开写回共享消息板并 @be
    board = swarm.read_messages(cfg, tail=1)
    assert "be" in board[-1].mentions
    assert "质检未通过" in board[-1].body


def test_monitor_detects_unanswered_and_stalled_and_nudges(tmp_path):
    cfg = _make_tower(tmp_path)
    swarm.add_workers(cfg, "ops", "巡检日志", auto_wake=False)
    # 仅发消息不自动唤醒，模拟漏回
    swarm.post_message(cfg, "@ops 请确认备份状态", auto_wake=False)

    check = swarm.monitor_check(cfg, nudge=False)
    assert check["healthy"] is False
    assert any(item["worker"] == "ops" for item in check["unanswered"])

    nudged = swarm.monitor_check(cfg, nudge=True)
    assert nudged["nudged"]
    # 催办后 ops 已回复，再检即健康
    assert swarm.monitor_check(cfg, nudge=False)["healthy"] is True


def test_cli_swarm_commands_and_demo(tmp_path, monkeypatch, capsys):
    cfg = _make_tower(tmp_path)
    monkeypatch.chdir(tmp_path)

    assert cli.main(["swarm", "title", "CLI 测试蜂群"]) == 0
    assert cli.main(["swarm", "models", "--all", "--json"]) == 0
    models_out = json.loads(capsys.readouterr().out.splitlines()[-1] if False else capsys.readouterr().out or "{}") if False else None
    # 重新捕获标准输出测试 JSON 完整性
    capsys.readouterr()
    assert cli.main(["swarm", "models", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert any(m["id"] == "custom-brain" for m in data["models"])

    assert cli.main(["swarm", "add", "dev", "--brief", "完成功能", "--workspace", "share", "--json"]) == 0
    added = json.loads(capsys.readouterr().out)
    assert added["ok"] is True

    assert cli.main(["swarm", "status", "--json"]) == 0
    snap = json.loads(capsys.readouterr().out)
    assert snap["title"] == "CLI 测试蜂群"
    assert snap["counts"]["waiting"] == 1

    # 离线演示不依赖当前目录
    assert cli.main(["swarm", "demo", "--json"]) == 0
    demo_res = json.loads(capsys.readouterr().out)
    assert demo_res["ok"] is True
    assert len(demo_res["steps"]) >= 6


@contextlib.contextmanager
def _serve_panel(cfg):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cfg))
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()
        t.join(timeout=5)


def test_panel_swarm_ui_and_api_with_write_guard(tmp_path):
    cfg = _make_tower(tmp_path)
    with _serve_panel(cfg) as port:
        # 1. GET /swarm 页面离线可用
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/swarm", timeout=5) as resp:
            html = resp.read().decode("utf-8")
            assert resp.status == 200
            assert '/assets/pico.css' in html
            assert "公开共享消息板" in html

        # 2. 跨站伪造 Origin POST 必须被 403 拦截
        evil_req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/swarm/post",
            data=json.dumps({"body": "hack"}).encode("utf-8"),
            headers={"Content-Type": "application/json", "Origin": "http://evil.example"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(evil_req, timeout=5)
        assert caught.value.code == 403

        # 3. 同源 POST 召唤工蜂 -> 发消息唤醒 -> 换模型 -> 质检
        origin = f"http://127.0.0.1:{port}"
        def _post(path: str, body: dict):
            req = urllib.request.Request(
                f"{origin}{path}",
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json", "Origin": origin},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode("utf-8"))

        code, res = _post("/api/swarm/add", {"name": "ui", "brief": "优化面板", "workspace": "share"})
        assert code == 200 and res["ok"] is True

        code, res = _post("/api/swarm/post", {"body": "@ui 请确认暗色主题对比度"})
        assert code == 200 and res["woken"] == ["ui"]

        code, res = _post("/api/swarm/action", {"action": "model", "name": "ui", "model": "mock-pro"})
        assert code == 200 and res["new_model"] == "mock-pro"

        code, res = _post("/api/swarm/action", {"action": "verify", "name": "ui", "land": True})
        assert code == 200 and res["ok"] is True

        with urllib.request.urlopen(f"{origin}/api/swarm", timeout=5) as resp:
            snap = json.loads(resp.read().decode("utf-8"))
            assert snap["counts"]["waiting"] == 1
            assert snap["workers"][0]["verified"] is True
