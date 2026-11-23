"""CLI UX defects reported 2026-11-23.

1. `tower role add --guided` printed "sidebar: 0 registered, 0 missing" when no
   session was configured at all — a false all-clear.
2. A dispatch refused by `[kickoff].require = true` raised ValueError out of
   cmd_dispatch, so the operator got a Python traceback instead of one line.
"""

from __future__ import annotations

from control_tower import cli
from control_tower.config import parse_config


def _write_tower(tmp_path, *extra: str) -> None:
    (tmp_path / "tower.toml").write_text(
        "\n".join(
            [
                "name: ux",
                f"workspace: {tmp_path.as_posix()}",
                "roles:",
                "  seed:",
                '    description: "existing role"',
                "    adapter: mock",
                *extra,
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def test_guided_role_add_says_when_no_session_is_wired(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DSH_HOME", str(tmp_path / "dsh-home"))
    _write_tower(tmp_path)

    code = cli.main(["role", "add", "--guided", "--template", "b2b-site"])
    out = capsys.readouterr()

    assert code == 0
    assert (
        "sidebar: 0 wired — run `tower bootstrap` to create sessions; "
        "the sidebar only shows roles that have one"
    ) in out.out
    assert "registered" not in out.out
    assert "missing" not in out.out


def test_refused_dispatch_is_one_line_on_stderr(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DSH_HOME", str(tmp_path / "dsh-home"))
    _write_tower(tmp_path, "", "kickoff:", "  require: true")
    assert parse_config(tmp_path / "tower.toml").kickoff_require

    for flags in ([], ["--async"]):
        code = cli.main(["dispatch", "seed", "fix the LCP", *flags])
        captured = capsys.readouterr()

        assert code == 2
        assert "Traceback" not in captured.err
        refused = [line for line in captured.err.splitlines()
                   if line.startswith("dispatch refused:")]
        assert len(refused) == 1
        assert "no task package" in refused[0]
