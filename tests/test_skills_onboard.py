"""Skills (playbook point 3), onboarding (points 1+6) and doctor hardening (2+4)."""

import json
import sys

import pytest

from control_tower import cli
from control_tower.board import Board, STATUS_ACCEPTED, STATUS_REPORTED
from control_tower.config import ensure_layout, load_config, parse_config
from control_tower.dispatch import render_prompt
from control_tower.onboard import onboard, parse_notes
from control_tower.skills import (
    rebuild_index,
    save_skill,
    skill_index_lines,
    slug_for,
)


@pytest.fixture()
def tower(tmp_path, monkeypatch):
    config = {
        "name": "test-tower",
        "workspace": str(tmp_path),
        "roles": {
            "backend": {"description": "server side", "adapter": "mock",
                        "write_scopes": ["roles/backend/"], "max_rounds": 2,
                        "env": {"DEPLOY_KEY": "x"}},
        },
        "verify_commands": [f'"{sys.executable}" -c "print(1)"'],
    }
    path = tmp_path / "tower.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    cfg = parse_config(path)
    ensure_layout(cfg)
    monkeypatch.chdir(tmp_path)
    return cfg


def _accepted_task(tower, brief="write the deploy script"):
    board = Board(tower)
    task = board.create("backend", brief)
    (tower.workspace / "roles" / "backend" / "out.txt").write_text("x", encoding="utf-8")
    task.report = {"state": "ready", "summary": "wrote the deploy script",
                   "files": ["roles/backend/out.txt"], "checks": ["bash deploy.sh → ok"]}
    board.save(task)
    board.transition(task, STATUS_ACCEPTED, note="verified")
    return task


# ------------------------------------------------------------------- skills


def test_only_an_accepted_task_becomes_a_skill(tower):
    board = Board(tower)
    reported = board.create("backend", "not accepted yet")
    reported.status = STATUS_REPORTED
    board.save(reported)
    with pytest.raises(ValueError, match="not accepted"):
        save_skill(tower, board, reported.id)


def test_save_skill_writes_file_and_index(tower):
    task = _accepted_task(tower)
    info = save_skill(tower, Board(tower), task.id)
    assert info["role"] == "backend"
    assert info["index_lines"], "the index must gain a line"
    body = (tower.role_dir("backend") / "skills" / f"{info['slug']}.md").read_text(encoding="utf-8")
    assert "## Steps" in body and "## Checks" in body
    assert "bash deploy.sh → ok" in body
    events = [json.loads(l)["event"] for l in tower.ledger_path.read_text(encoding="utf-8").splitlines()]
    assert "skill_saved" in events


def test_dispatch_injects_the_skill_index_not_the_body(tower):
    task = _accepted_task(tower)
    save_skill(tower, Board(tower), task.id)
    new_task = Board(tower).create("backend", "write another deploy script")
    prompt = render_prompt(tower, new_task)
    assert "## Skills from jobs you already did well" in prompt
    assert "write-the-deploy-script" in prompt          # the index line
    assert "passed verification" not in prompt          # never the body
    assert "## Restatement" not in prompt               # and not another gate's text


def test_rebuild_index_drops_the_line_when_the_skill_file_is_gone(tower):
    task = _accepted_task(tower)
    info = save_skill(tower, Board(tower), task.id)
    assert skill_index_lines(tower, "backend")
    (tower.role_dir("backend") / "skills" / f"{info['slug']}.md").unlink()
    assert rebuild_index(tower, "backend") == []
    assert skill_index_lines(tower, "backend") == []


def test_slug_is_readable_and_never_empty():
    assert slug_for("T-0001", "Fix the Login Bug!") == "fix-the-login-bug"
    assert slug_for("T-0002", "!!!") == "task-t-0002"


def test_cli_skill_save_and_list(tower, capsys):
    task = _accepted_task(tower)
    assert cli.main(["skill", "save", task.id]) == 0
    out = capsys.readouterr().out
    assert "skill saved" in out
    assert cli.main(["skill", "list"]) == 0
    assert "write-the-deploy-script" in capsys.readouterr().out


# ------------------------------------------------------------------ onboard


def test_parse_notes_reads_roles_scopes_and_plan():
    notes = """# What I do
I run a small studio.

## backend
builds the server side
scope: src/server/
scope: tests/

## frontend
builds the browser UI
scope: src/web/
"""
    roles, plan = parse_notes(notes)
    assert [r["name"] for r in roles] == ["backend", "frontend"]
    assert roles[0]["write_scopes"] == ["src/server/", "tests/"]
    assert roles[0]["description"] == "builds the server side"
    assert "I run a small studio." in plan


def _init_tower(tmp_path):
    """A real tower.toml workspace: role editing is line-based on YAML only."""
    root = tmp_path / "ws"
    root.mkdir()
    assert cli.main(["init", str(root)]) == 0
    return root


def test_onboard_grows_the_team_and_writes_the_plan(tmp_path):
    root = _init_tower(tmp_path)
    notes = tmp_path / "notes.md"
    notes.write_text(
        "# What I do\na small studio\n\n## reviewer\nverifies claims\nscope: reports/\n",
        encoding="utf-8",
    )
    result = onboard(root / "tower.toml", notes_path=notes, adapter="mock")
    assert result["roles"] == ["reviewer"]
    cfg = load_config(root)
    assert "reviewer" in cfg.roles
    assert cfg.roles["reviewer"].write_scopes == ["reports/"]
    plan = (root / "PLAN.md").read_text(encoding="utf-8")
    assert "a small studio" in plan and "**reviewer**" in plan


def test_onboard_enforces_few_well_trained_bots(tmp_path):
    root = _init_tower(tmp_path)
    notes = tmp_path / "notes.md"
    sections = "".join(f"\n## role{i}\ndoes thing {i}\n" for i in range(7))
    notes.write_text(sections, encoding="utf-8")
    with pytest.raises(ValueError, match="few, well-trained"):
        onboard(root / "tower.toml", notes_path=notes)


def test_onboard_without_notes_needs_a_terminal(tmp_path):
    root = _init_tower(tmp_path)
    monkey = pytest.MonkeyPatch()
    monkey.setattr("sys.stdin.isatty", lambda: False)
    try:
        with pytest.raises(ValueError, match="--from"):
            onboard(root / "tower.toml")
    finally:
        monkey.undo()


def test_cli_onboard_from_notes(tmp_path, capsys):
    root = _init_tower(tmp_path)
    notes = tmp_path / "notes.md"
    notes.write_text("## reviewer\nwrites the posts\nscope: blog/\n", encoding="utf-8")
    rc = cli.main(["--workspace", str(root), "onboard", "--from", str(notes), "--adapter", "mock"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "reviewer" in out
    cfg = load_config(root)
    assert "reviewer" in cfg.roles


# ------------------------------------------------------- doctor hardening


def test_doctor_warns_on_overlap_size_and_global_credentials(tower, capsys):
    # second role sharing backend's scope -> overlap warning
    (tower.workspace / "tower.json").write_text(json.dumps({
        "name": "test-tower", "workspace": str(tower.workspace),
        "roles": {
            "backend": {"description": "a", "adapter": "mock",
                        "write_scopes": ["roles/shared/"], "env": {"K": "v"}},
            "frontend": {"description": "b", "adapter": "mock",
                         "write_scopes": ["roles/shared/"]},
        },
        "verify_commands": [],
    }), encoding="utf-8")
    assert cli.main(["doctor"]) == 0          # warnings, not problems
    out = capsys.readouterr().out
    assert "both write" in out
    assert "roles/backend/.env" in out


def test_doctor_warns_on_a_crowd_of_roles(tower, capsys):
    roles = {f"r{i}": {"description": "x", "adapter": "mock"} for i in range(6)}
    (tower.workspace / "tower.json").write_text(json.dumps({
        "name": "t", "workspace": str(tower.workspace), "roles": roles,
        "verify_commands": [],
    }), encoding="utf-8")
    assert cli.main(["doctor"]) == 0
    assert "well-trained bots beat a crowd" in capsys.readouterr().out
