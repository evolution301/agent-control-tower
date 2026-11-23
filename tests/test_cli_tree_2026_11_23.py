"""`tower role list --tree` (2026-11-23): flat towers list every role; layered
towers indent members under their leader. Regression cover for the row["name"]
KeyError that printed only `error: 'name'`."""

from __future__ import annotations

from control_tower import cli


def _role(name: str, **options: object) -> str:
    lines = [f"  {name}:", f'    description: "{name}"', "    adapter: mock"]
    for key, value in options.items():
        lines.append(f"    {key}: {'true' if value is True else value}")
    return "\n".join(lines)


def _tower(tmp_path, *roles: str) -> None:
    (tmp_path / "tower.toml").write_text(
        "\n".join(["name: ux", f"workspace: {tmp_path.as_posix()}", "roles:", *roles]) + "\n",
        encoding="utf-8",
    )


def test_flat_tower_lists_every_role_with_missing_sidebar(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DSH_HOME", str(tmp_path / "dsh-home"))
    _tower(tmp_path, _role("alpha"), _role("beta"))

    code = cli.main(["role", "list", "--tree"])
    out = capsys.readouterr().out

    assert code == 0
    assert "flat tower" in out
    assert "· alpha  sidebar:MISSING" in out
    assert "· beta  sidebar:MISSING" in out


def test_layered_tower_indents_member_under_leader(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DSH_HOME", str(tmp_path / "dsh-home"))
    _tower(tmp_path, _role("lead", leader=True), _role("ops", report_to="lead"))

    code = cli.main(["role", "list", "--tree"])
    lines = capsys.readouterr().out.splitlines()

    assert code == 0
    assert "flat tower" not in "\n".join(lines)
    assert "● lead  sidebar:MISSING" in lines
    assert [line for line in lines if "ops" in line] == ["  · ops  sidebar:MISSING"]
