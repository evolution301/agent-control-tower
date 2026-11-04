"""Tests for conversational role (bot) management: add, list, remove.

The lead agent builds the team by conversation, so these edits happen from an
agent loop: they must never corrupt tower.toml, and must keep everything the
operator wrote by hand (comments, order, other roles).
"""

import json

import pytest

from control_tower.config import load_config
from control_tower.roles import add_role, list_roles, remove_role

BASE_CONFIG = """# hand-written comments must survive agent edits
name: probe
workspace: .

roles:
  # the only role at the start
  backend:
    description: "server side"
    adapter: mock
    write_scopes: ["src/"]
    max_rounds: 2

verify_commands:
  - "echo ok"

adapters:
  dsh:
    base_url: "http://127.0.0.1:1"
    sessions: {}
"""


@pytest.fixture()
def cfg_path(tmp_path):
    path = tmp_path / "tower.toml"
    path.write_text(BASE_CONFIG, encoding="utf-8")
    return path


def test_add_role_writes_config_charter_and_session(cfg_path):
    info = add_role(
        cfg_path,
        "keyword-research",
        description="关键词调研",
        write_scopes=["research/keywords/"],
        session_id="session-abc",
    )
    text = cfg_path.read_text(encoding="utf-8")
    # the new role is in, the old one and the comments are untouched
    assert "keyword-research:" in text
    assert "backend:" in text
    assert "# hand-written comments must survive agent edits" in text
    assert "# the only role at the start" in text
    cfg = load_config(cfg_path.parent)
    assert set(cfg.roles) == {"backend", "keyword-research"}
    assert cfg.roles["keyword-research"].write_scopes == ["research/keywords/"]
    assert (cfg.adapter_options["dsh"]["sessions"]) == {"keyword-research": "session-abc"}
    assert info["replaced"] is False
    assert (cfg_path.parent / "roles" / "keyword-research" / "AGENT.md").exists()


def test_add_role_refuses_duplicates_without_force(cfg_path):
    with pytest.raises(ValueError):
        add_role(cfg_path, "backend", description="dup")
    info = add_role(cfg_path, "backend", description="replaced", force=True)
    assert info["replaced"] is True
    cfg = load_config(cfg_path.parent)
    assert cfg.roles["backend"].description == "replaced"
    # replacing must not duplicate the block
    assert cfg_path.read_text(encoding="utf-8").count("  backend:") == 1


def test_add_role_validates_name_and_adapter(cfg_path):
    with pytest.raises(ValueError):
        add_role(cfg_path, "no spaces allowed")
    with pytest.raises(ValueError):
        add_role(cfg_path, "ok-name", adapter="telepathy")


def test_add_role_into_config_without_roles_section(tmp_path):
    path = tmp_path / "tower.toml"
    path.write_text("name: bare\nworkspace: .\n", encoding="utf-8")
    add_role(path, "qa", description="verification")
    cfg = load_config(tmp_path)
    assert set(cfg.roles) == {"qa"}


def test_remove_role_drops_block_and_session(cfg_path):
    add_role(cfg_path, "temp", description="temp", session_id="session-temp")
    info = remove_role(cfg_path, "temp")
    assert info["purged"] is None
    text = cfg_path.read_text(encoding="utf-8")
    assert "temp:" not in text
    assert "session-temp" not in text
    assert "backend:" in text
    cfg = load_config(cfg_path.parent)
    assert set(cfg.roles) == {"backend"}


def test_remove_role_purge_deletes_the_charter_dir(cfg_path):
    add_role(cfg_path, "temp", description="temp")
    role_dir = cfg_path.parent / "roles" / "temp"
    assert role_dir.exists()
    remove_role(cfg_path, "temp", purge=True)
    assert not role_dir.exists()


def test_remove_unknown_role_raises(cfg_path):
    with pytest.raises(ValueError):
        remove_role(cfg_path, "ghost")


def test_list_roles_reports_wired_sessions(cfg_path):
    add_role(cfg_path, "copy", description="copywriting", session_id="session-copy")
    rows = list_roles(load_config(cfg_path.parent))
    names = [row["name"] for row in rows]
    assert names == ["backend", "copy"]
    copy_row = next(row for row in rows if row["name"] == "copy")
    assert copy_row["session_id"] == "session-copy"
    backend_row = next(row for row in rows if row["name"] == "backend")
    assert backend_row["session_id"] == ""


def test_role_list_json_is_machine_readable(cfg_path, capsys):
    from control_tower import cli

    assert cli.main(["--workspace", str(cfg_path.parent), "role", "add", "copy", "--session", "s-1", "--json"]) == 0
    capsys.readouterr()
    assert cli.main(["--workspace", str(cfg_path.parent), "role", "list", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert {row["name"] for row in rows} == {"backend", "copy"}
    assert next(row for row in rows if row["name"] == "copy")["session_id"] == "s-1"
