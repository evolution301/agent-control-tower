"""T-0051: claiming a pre-registered DSH session, and pure `kickoff --json`.

The pool record is written by `tower bootstrap` (runtime lane); these tests pin
the cli side: claim moves the id out of `free` into `claimed` and wires the
role in tower.toml, the next claim takes the next id, and an empty pool keeps
today's restart-hint behaviour. The last test runs the real module in a
subprocess, because the defect was pipes on stdout: a huddle that prints
progress corrupted `json.load`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from control_tower import cli
from control_tower.config import parse_config

REPO = Path(__file__).resolve().parents[1]


def _write_tower(tmp_path, *roles: str) -> None:
    lines = ["name: pool", f"workspace: {tmp_path.as_posix()}", "roles:"]
    for name in roles or ("seed",):
        lines += [f"  {name}:", "    adapter: mock"]
    (tmp_path / "tower.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_pool(tmp_path, free, claimed=None) -> Path:
    path = tmp_path / ".tower" / "dsh_pool.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"free": free, "claimed": claimed or {}}), encoding="utf-8")
    return path


def _sessions(tmp_path) -> dict:
    return (parse_config(tmp_path / "tower.toml").adapter_options.get("dsh") or {}).get("sessions") or {}


def test_claim_moves_a_free_id_into_tower_toml_then_takes_the_next(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TOWER_HOME", str(tmp_path / "home"))
    _write_tower(tmp_path)
    pool = _write_pool(tmp_path, ["S-1", {"session": "S-2", "title": "spare"}])

    assert cli.main(["role", "add", "backend", "--adapter", "dsh"]) == 0
    out = capsys.readouterr().out
    assert "claimed pooled session — visible now, no restart needed" in out
    assert _sessions(tmp_path)["backend"] == "S-1"

    record = json.loads(pool.read_text(encoding="utf-8"))
    assert record["free"] == [{"session": "S-2", "title": "spare"}]
    assert record["claimed"]["backend"] == {"session": "S-1"}

    assert cli.main(["role", "add", "qa", "--adapter", "dsh"]) == 0
    assert _sessions(tmp_path)["qa"] == "S-2"
    record = json.loads(pool.read_text(encoding="utf-8"))
    assert record["free"] == []
    assert set(record["claimed"]) == {"backend", "qa"}


def test_force_readd_keeps_the_role_on_the_id_it_already_claimed(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TOWER_HOME", str(tmp_path / "home"))
    _write_tower(tmp_path)
    pool = _write_pool(tmp_path, ["S-1", "S-2"])

    assert cli.main(["role", "add", "backend", "--adapter", "dsh"]) == 0
    assert _sessions(tmp_path)["backend"] == "S-1"
    capsys.readouterr()

    assert cli.main(["role", "add", "backend", "--adapter", "dsh", "--force"]) == 0
    assert _sessions(tmp_path)["backend"] == "S-1"
    record = json.loads(pool.read_text(encoding="utf-8"))
    assert record["free"] == ["S-2"]
    assert record["claimed"]["backend"] == {"session": "S-1"}


def test_empty_pool_falls_back_to_the_restart_hint(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TOWER_HOME", str(tmp_path / "home"))
    _write_tower(tmp_path)

    assert cli.main(["role", "add", "frontend", "--adapter", "dsh"]) == 0
    out = capsys.readouterr().out
    assert "claimed pooled session" not in out
    assert "next: tower bootstrap" in out
    assert "frontend" not in _sessions(tmp_path)


def test_register_apply_claims_the_pool_and_writes_the_registry(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TOWER_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("DSH_HOME", str(tmp_path / "dsh"))
    (tmp_path / "tower.toml").write_text(
        "\n".join([
            "name: pool",
            f"workspace: {tmp_path.as_posix()}",
            "roles:",
            "  seed:",
            "    adapter: mock",
            "  backend:",
            "    adapter: dsh",
        ]) + "\n",
        encoding="utf-8",
    )
    _write_pool(tmp_path, ["S-7"])
    registry = tmp_path / "dsh" / "storages" / "workspace.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(
        json.dumps({"tables": {"workspaces": {"w1": {"path": str(tmp_path), "sessionIds": []}}}}),
        encoding="utf-8",
    )

    assert cli.main(["dsh", "register", "--apply"]) == 0
    out = capsys.readouterr().out
    assert "claimed pooled session — visible now, no restart needed" in out
    assert "written:" in out
    assert _sessions(tmp_path)["backend"] == "S-7"
    assert "seed" not in _sessions(tmp_path)
    assert json.loads(registry.read_text(encoding="utf-8"))["tables"]["workspaces"]["w1"]["sessionIds"] == ["S-7"]


def test_pool_list_shows_free_and_claimed_and_explains_an_uninitialised_pool(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TOWER_HOME", str(tmp_path / "home"))
    _write_tower(tmp_path)

    assert cli.main(["dsh", "pool", "list"]) == 0
    missing = capsys.readouterr().out
    assert (
        "pool not initialised — run tower bootstrap with adapters.dsh.pool_size > 0"
        in missing
    )

    _write_pool(
        tmp_path,
        ["S-1", {"session": "S-2", "title": "spare"}],
        {"backend": {"session": "S-9", "title": "Backend"}},
    )
    assert cli.main(["dsh", "pool", "list"]) == 0
    out = capsys.readouterr().out
    assert "free" in out and "S-1" in out
    assert "S-2" in out and "spare" in out
    assert "claimed" in out and "S-9" in out and "backend" in out


def test_kickoff_json_moves_huddle_progress_to_stderr(tmp_path, monkeypatch, capsys):
    from control_tower import kickoff as kickoff_mod

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TOWER_HOME", str(tmp_path / "home"))
    _write_tower(tmp_path, "a")
    brief = tmp_path / "brief.md"
    brief.write_text("fix the signup bug", encoding="utf-8")
    summary = {"state": "ok", "absent": [], "seconds": 1}

    def fake_kickoff(cfg, board, text, roles, title="", discuss=True):
        print("discussing with " + ", ".join(roles))
        print("warning: huddle took 1s")
        return {
            "group": {"id": "G-1", "roles": roles},
            "package": {"id": "P-1", "discuss": summary},
            "path": "P-1.json",
            "minutes": [],
            "problems": [],
            "discuss": summary,
        }

    monkeypatch.setattr(kickoff_mod, "kickoff", fake_kickoff)
    assert cli.main(["kickoff", "--brief-file", str(brief), "--role", "a", "--json"]) == 0
    out = capsys.readouterr()

    payload = json.loads(out.out)
    assert payload["discuss"] == summary
    assert payload["package"]["discuss"] == summary
    assert "discussing with a" in out.err
    assert "warning: huddle took 1s" in out.err


def test_kickoff_json_stdout_is_one_document_even_when_the_huddle_prints(
    tmp_path, monkeypatch
):
    _write_tower(tmp_path, "a", "b")
    brief = tmp_path / "brief.md"
    brief.write_text("fix the signup bug", encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    env["TOWER_HOME"] = str(tmp_path / "home")
    env["DSH_HOME"] = str(tmp_path / "dsh-home")
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    proc = subprocess.run(
        [
            sys.executable, "-m", "control_tower", "--workspace", str(tmp_path),
            "kickoff", "--brief-file", str(brief), "--title", "t",
            "--role", "a", "--role", "b", "--discuss", "--json",
        ],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120,
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert isinstance(payload["discuss"], dict)
    assert set(payload["discuss"]) >= {"state", "absent", "seconds"}
    assert payload["package"]["discuss"] == payload["discuss"]
