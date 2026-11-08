"""Charter + standing-rules injection (the dead AGENT.md file now rides every dispatch)."""

import json

import pytest

from control_tower.config import ensure_layout, parse_config
from control_tower.dispatch import render_prompt


@pytest.fixture()
def tower(tmp_path):
    config = {
        "name": "charter-tower",
        "workspace": str(tmp_path),
        "roles": {"seo": {"description": "s", "adapter": "mock"}},
    }
    path = tmp_path / "tower.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    cfg = parse_config(path)
    ensure_layout(cfg)
    (tmp_path / "roles" / "seo").mkdir(parents=True, exist_ok=True)
    return cfg


def _task(tower, brief="work"):
    from control_tower.board import Board

    board = Board(tower)
    task = board.create("seo", brief)
    board.save(task)
    return render_prompt(tower, task)


def test_role_charter_is_injected_and_truncated_at_1500(tower):
    charter = "你是独立审查者。" + "很长的章程" * 400
    (tower.workspace / "roles" / "seo" / "AGENT.md").write_text(
        charter, encoding="utf-8")
    prompt = _task(tower)
    assert "## Role charter" in prompt
    assert "你是独立审查者。" in prompt
    assert "…(truncated)" in prompt
    assert len(prompt) < len(charter) + 4000, "injected section is truncated"


def test_standing_rules_from_bug_report_protocol(tower):
    rules = "# 塔台 Bug 上报协议\n\n一句话：发现 bug 上报维护者会话。"
    docs = tower.workspace / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "bug-report-protocol.md").write_text(rules, encoding="utf-8")
    prompt = _task(tower)
    assert "## Standing rules" in prompt
    assert "上报维护者会话" in prompt


def test_rules_fallback_order_protocol_then_rules_md_then_rules(tower):
    (tower.workspace / "RULES.md").write_text("from RULES.md", encoding="utf-8")
    prompt = _task(tower)
    assert "from RULES.md" in prompt
    docs = tower.workspace / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "bug-report-protocol.md").write_text("protocol wins", encoding="utf-8")
    prompt = _task(tower)
    assert "protocol wins" in prompt and "from RULES.md" not in prompt


def test_no_charter_no_rules_means_no_empty_sections(tower):
    prompt = _task(tower)
    assert "## Role charter" not in prompt
    assert "## Standing rules" not in prompt
