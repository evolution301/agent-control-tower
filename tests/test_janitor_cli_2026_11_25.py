"""`tower janitor` CLI wiring (T-0063): dry-run, --apply, rule 6, integrations."""

import json
import os
import time
from pathlib import Path

from control_tower import cli, registry
from control_tower.board import Board, Task
from control_tower.config import ensure_layout, parse_config


def _ago(seconds: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(time.time() - seconds))


def _tower(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "tower.json"
    path.write_text(json.dumps({
        "name": "janitor-cli",
        "workspace": str(ws),
        "roles": {"core": {"adapter": "mock"}, "cli": {"adapter": "mock"}},
    }), encoding="utf-8")
    cfg = parse_config(path)
    ensure_layout(cfg)
    return cfg


def _task(cfg, task_id, *, role="core", status="rework", age=90000.0, brief="stale work",
          scopes=()):
    data = Task(
        id=task_id,
        role=role,
        brief=brief,
        status=status,
        created_at=_ago(age + 3600),
        updated_at=_ago(age),
        write_scopes=list(scopes),
    )
    (cfg.tasks_dir / f"{task_id}.json").write_text(
        json.dumps(data.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return data


def _heartbeat(cfg, age=0.0):
    cfg.queue_dir.mkdir(parents=True, exist_ok=True)
    heart = cfg.queue_dir / "worker.json"
    heart.write_text("{}", encoding="utf-8")
    stamp = time.time() - age
    os.utime(heart, (stamp, stamp))


def _report(cfg, task_id="T-0003", role="core"):
    artifact = cfg.role_dir(role) / "report.txt"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("done\n", encoding="utf-8")
    path = cfg.roles_dir / role / f"{task_id}-REPORT.md"
    path.write_text(
        "## Status\nready\n\n## Summary\nStale but finished.\n\n"
        "## Files\n- roles/core/report.txt — wrote it\n\n"
        "## Checks\n- python --version → exit 0\n\n"
        "## Self-review\nno findings\n",
        encoding="utf-8",
    )
    stamp = time.time() - 8000
    os.utime(path, (stamp, stamp))


def _status(cfg, task_id):
    return Board(cfg).load(task_id).status


def _ledger(cfg):
    path = cfg.state_dir / "ledger.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _run(capsys, tmp_path, *argv):
    code = cli.main(["--workspace", str(tmp_path), *argv])
    out, err = capsys.readouterr()
    return code, out, err


def test_dry_run_table_and_summary(capsys, tmp_path):
    cfg = _tower(tmp_path)
    _task(cfg, "T-0001", role="ghost", status="reported")
    _task(cfg, "T-0002")

    code, out, _ = _run(capsys, tmp_path, "janitor", "--older-than", "0s")

    assert code == 0
    assert "id" in out and "suggested action" in out and "evidence" in out
    assert "T-0001" in out and "T-0002" in out
    assert "2 zombies (rule 1: 1, rule 2: 1)" in out
    assert _status(cfg, "T-0001") == "reported"
    assert _status(cfg, "T-0002") == "rework"


def test_apply_drops_rule_hits_with_the_ledger_note(capsys, tmp_path):
    cfg = _tower(tmp_path)
    _task(cfg, "T-0001", role="ghost", status="reported")
    _task(cfg, "T-0002")

    code, out, _ = _run(capsys, tmp_path, "janitor", "--older-than", "0s", "--apply")

    assert code == 0
    assert _status(cfg, "T-0001") == "failed"
    assert _status(cfg, "T-0002") == "failed"
    notes = [entry.get("note", "") for entry in _ledger(cfg)
             if entry.get("event") == "attention_resolved"]
    assert any(note.startswith("janitor: rule #1 | ") for note in notes)
    assert any(note.startswith("janitor: rule #2 | ") for note in notes)
    assert "T-0001 dropped — janitor: rule #1" in out


def test_limit_refuses_to_apply_a_partial_scan(capsys, tmp_path):
    cfg = _tower(tmp_path)
    _task(cfg, "T-0001", role="ghost", status="reported")
    _task(cfg, "T-0002", role="ghost", status="reported")

    code, out, _ = _run(capsys, tmp_path, "janitor", "--older-than", "0s",
                        "--limit", "1", "--apply")

    assert code == 2
    assert "found 2 > limit" in out
    assert _status(cfg, "T-0001") == "reported"
    assert _status(cfg, "T-0002") == "reported"


def test_rule6_is_advisory_until_accept_reports(capsys, tmp_path):
    cfg = _tower(tmp_path)
    _heartbeat(cfg)
    _report(cfg)
    _task(cfg, "T-0003", status="reported")

    first = _run(capsys, tmp_path, "janitor", "--older-than", "0s", "--apply")
    assert first[0] == 0
    assert "T-0003 kept (rule 6 advisory" in first[1]
    assert _status(cfg, "T-0003") == "reported"

    second = _run(capsys, tmp_path, "janitor", "--older-than", "0s", "--apply",
                  "--accept-reports")
    assert second[0] == 0
    assert "T-0003 ACCEPTED" in second[1]
    assert _status(cfg, "T-0003") == "accepted"


def test_doctor_reports_zombies_and_clears_it(capsys, tmp_path):
    cfg = _tower(tmp_path)
    _task(cfg, "T-0001", role="ghost", status="reported")

    code, out, _ = _run(capsys, tmp_path, "doctor")
    assert code == 1
    assert "1 zombie tasks (run tower janitor)" in out

    (cfg.tasks_dir / "T-0001.json").unlink()
    code, out, _ = _run(capsys, tmp_path, "doctor")
    assert "zombie tasks" not in out


def test_projects_marks_open_count_with_zombies(capsys, tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("TOWER_HOME", str(home))
    cfg = _tower(tmp_path)
    _task(cfg, "T-0001", role="ghost", status="reported")
    other = tmp_path / "other"
    other.mkdir()
    (other / "tower.json").write_text(json.dumps({
        "name": "other", "workspace": str(other / "ws"),
        "roles": {"core": {"adapter": "mock"}},
    }), encoding="utf-8")
    registry.save_projects([
        {"name": "janitor-cli", "path": str(tmp_path), "color": "#111111"},
        {"name": "other", "path": str(other), "color": "#222222"},
    ])

    code, out, _ = _run(capsys, tmp_path, "projects", "list")

    assert code == 0
    bot_lines = [row for row in out.splitlines() if row.strip().startswith("bots ")]
    assert len(bot_lines) == 2
    assert "open 1 (zombie 1)" in bot_lines[0]
    assert "open 0" in bot_lines[1] and "zombie" not in bot_lines[1]


def test_multiline_note_and_note_file_round_trip(capsys, tmp_path):
    cfg = _tower(tmp_path)
    _task(cfg, "T-0001", status="reported")
    _task(cfg, "T-0002", status="reported")
    _task(cfg, "T-0003", status="reported")

    code = cli.main(["--workspace", str(tmp_path), "attention", "resolve", "T-0001",
                     "--note", "line one\nline two"])
    assert code == 0
    long_note = tmp_path / "note.md"
    long_note.write_text("first\nsecond\nthird\n", encoding="utf-8")
    assert cli.main(["--workspace", str(tmp_path), "attention", "resolve", "T-0002",
                     "--note-file", str(long_note)]) == 0
    big_note = tmp_path / "big.md"
    big_note.write_text("\n".join(f"line {i} " + "x" * 10 for i in range(30))
                        + "\nTAIL-MARKER\n", encoding="utf-8")
    assert cli.main(["--workspace", str(tmp_path), "attention", "resolve", "T-0003",
                     "--note-file", str(big_note)]) == 0

    notes = [entry.get("note", "") for entry in _ledger(cfg)
             if entry.get("event") == "attention_resolved"]
    assert "line one\nline two" in notes
    assert "first\nsecond\nthird" in notes
    assert any("TAIL-MARKER" in entry.get("note", "") for entry in _ledger(cfg)
               if entry.get("event") == "attention_note")
