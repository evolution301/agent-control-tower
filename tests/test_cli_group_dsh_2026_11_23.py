"""Live defects on 0.18.0 (2026-11-23): `group show --json` dropped the package,
and `dsh register --apply` claimed there was no registry after writing it."""

from __future__ import annotations

import json
from pathlib import Path

from control_tower import cli, groups, kickoff


def _tower(tmp_path, sessions: dict[str, str] | None = None) -> None:
    lines = [
        "name: ux",
        f"workspace: {tmp_path.as_posix()}",
        "roles:",
        "  lead:",
        '    description: "lead"',
        f"    adapter: {'dsh' if sessions else 'mock'}",
    ]
    if sessions:
        lines += [
            "adapters:",
            "  dsh:",
            '    base_url: "http://127.0.0.1:1"',
            "    sessions:",
            *[f"      {role}: {session}" for role, session in sessions.items()],
        ]
    (tmp_path / "tower.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _registry(tmp_path, session_ids: list[str]) -> Path:
    home = tmp_path / "dsh-home"
    (home / "storages").mkdir(parents=True)
    data = {"tables": {"workspaces": {
        "w1": {"path": str(tmp_path), "title": "ux", "sessionIds": session_ids},
    }}}
    path = home / "storages" / "workspace.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _cfg(tmp_path):
    from control_tower.config import parse_config

    return parse_config(tmp_path / "tower.toml")


def test_group_show_json_returns_the_real_package(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DSH_HOME", str(tmp_path / "dsh-home"))
    _tower(tmp_path)
    cfg = _cfg(tmp_path)
    group = groups.create(cfg, "one room", ["lead"])
    package = kickoff.template(cfg, group.id, group.title, "a brief", ["lead"])
    kickoff.save_package(cfg, group.id, package)

    code = cli.main(["group", "show", group.id, "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["package"]["id"] == package["id"]
    assert payload["package_draft"] is True


def test_register_apply_reports_the_written_registry(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    registry = _registry(tmp_path, [])
    monkeypatch.setenv("DSH_HOME", str(tmp_path / "dsh-home"))
    _tower(tmp_path, sessions={"lead": "S-LEAD"})

    code = cli.main(["dsh", "register"])
    out = capsys.readouterr().out
    assert code == 0
    assert "would add: S-LEAD" in out
    assert "written:" not in out

    code = cli.main(["dsh", "register", "--apply"])
    out = capsys.readouterr().out

    assert code == 0
    assert f"written: {registry}" in out
    assert "no DSH workspace registry" not in out
    written = json.loads(registry.read_text(encoding="utf-8"))
    assert "S-LEAD" in written["tables"]["workspaces"]["w1"]["sessionIds"]


def test_missing_registry_exits_3(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DSH_HOME", str(tmp_path / "dsh-home"))
    _tower(tmp_path, sessions={"lead": "S-LEAD"})

    code = cli.main(["dsh", "register"])
    out = capsys.readouterr().out

    assert code == 3
    assert "no DSH workspace registry" in out
