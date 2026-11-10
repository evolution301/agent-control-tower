"""No silent fallback to the global active project (2026-11-10 multi-tower bug)."""

import json
import pathlib

import pytest

from control_tower import cli, registry
from control_tower.config import ensure_layout, parse_config


def _make_tower(directory: pathlib.Path, name: str, role: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    config = {
        "name": name,
        "workspace": str(directory),
        "roles": {role: {"description": role, "adapter": "mock"}},
    }
    (directory / "tower.json").write_text(json.dumps(config), encoding="utf-8")


def test_cli_without_a_tower_errors_instead_of_using_the_pointer(tmp_path, monkeypatch, capsys):
    # an "active" tower exists elsewhere and is registered + bound + active
    other = tmp_path / "other-tower"
    _make_tower(other, "other", "auditor")
    registry.add_project(other, name="other")
    registry.set_active("other")

    cwd = tmp_path / "site-without-tower"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    code = cli.main(["say", "auditor", "hi"])
    captured = capsys.readouterr()
    assert code == 2, "no tower in cwd must be an error, not a silent redirect"
    assert "--workspace" in captured.err
    assert "active-project pointer" in captured.err
    # nothing was written into the other tower
    assert not list((other / ".tower").glob("tasks/*.json")) if (other / ".tower").exists() else True


def test_workspace_flag_still_wins_and_announces_the_tower(tmp_path, monkeypatch, capsys):
    tower = tmp_path / "my-tower"
    _make_tower(tower, "mine", "seo")
    monkeypatch.chdir(tmp_path)          # cwd has no config

    assert cli.main(["--workspace", str(tower), "dispatch", "seo", "hello"]) == 0
    captured = capsys.readouterr()
    assert "resolved workspace =" in captured.err
    assert str(tower) in captured.err
    tasks = list((tower / ".tower" / "tasks").glob("T-*.json"))
    assert len(tasks) == 1


def test_write_commands_announce_even_when_cwd_has_the_config(tmp_path, monkeypatch, capsys):
    _make_tower(tmp_path, "here", "seo")
    monkeypatch.chdir(tmp_path)
    assert cli.main(["dispatch", "seo", "announce me"]) == 0
    captured = capsys.readouterr()
    assert f"resolved workspace = {tmp_path}" in captured.err
