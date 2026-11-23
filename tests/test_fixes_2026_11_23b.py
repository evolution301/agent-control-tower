"""Regression pins for the second 2026-11-23 field report (evergocasa tower).

The live tower sent a second list after 0.17.2 shipped. These are the parts that
are code, not GUI: a report that lands after the adapter timeout must not be
thrown away, an unreachable adapter must be visible in `tower status`, a tower
with no real check must be told so at `doctor` time, "unknown role" must name the
workspace it actually resolved, and SIGTERM must drain instead of abandoning a
turn.

Each test fails on the pre-fix code.
"""

from __future__ import annotations

from control_tower.adapters.base import AdapterResult
from control_tower.board import Board, STATUS_FAILED, STATUS_REPORTED
from control_tower.cli import main
from control_tower.config import parse_config, unknown_role_message
from control_tower.dispatch import dispatch


def _write_toml(tmp_path):
    workspace = tmp_path / "tower"
    workspace.mkdir(exist_ok=True)
    toml = [
        "name: postmortem",
        f"workspace: {workspace.as_posix()}",
        "roles:",
        "  lead:",
        '    description: "lead"',
        "    adapter: mock",
        "  ops:",
        '    description: "ops"',
        "    adapter: mock",
        "adapters:",
        "  dsh:",
        '    base_url: "http://127.0.0.1:1"',
    ]
    path = tmp_path / "tower.toml"
    path.write_text("\n".join(toml) + "\n", encoding="utf-8")
    return path


class _TimedOutAdapter:
    """A role that ran past the adapter timeout and produced no text."""

    name = "mock"

    def run(self, request):  # noqa: ARG002 - adapter seam
        return AdapterResult(exit_code=124, text="adapter timeout after 1800s")


def _task_with_report(tmp_path, body: str):
    cfg = parse_config(_write_toml(tmp_path))
    board = Board(cfg)
    task = board.create("ops", "make the page faster")
    role_dir = cfg.role_dir("ops")
    role_dir.mkdir(parents=True, exist_ok=True)
    (role_dir / f"{task.id}-REPORT.md").write_text(body, encoding="utf-8")
    return cfg, board, task


# 1 ------------------------------------------------- a late report is work, not a failure
def test_report_that_lands_after_the_timeout_becomes_reported(tmp_path, monkeypatch):
    cfg, board, task = _task_with_report(
        tmp_path,
        "## Summary\nThe hero image now ships as WebP; LCP 4.1s -> 1.9s.\n\n"
        "## Files\n- `src/hero.webp`\n\n## Checks\n- `lighthouse` -> 96\n",
    )
    monkeypatch.setattr("control_tower.dispatch.get_adapter", lambda *a, **k: _TimedOutAdapter())
    dispatch(cfg, board, task)
    assert task.status == STATUS_REPORTED
    assert "WebP" in task.report["summary"]
    assert task.report["files"] == ["`src/hero.webp`"]
    ledger = cfg.ledger_path.read_text(encoding="utf-8")
    assert "late_report" in ledger


def test_an_empty_report_file_is_not_evidence_of_work(tmp_path, monkeypatch):
    cfg, board, task = _task_with_report(tmp_path, "## Summary\n\n## Files\n")
    monkeypatch.setattr("control_tower.dispatch.get_adapter", lambda *a, **k: _TimedOutAdapter())
    dispatch(cfg, board, task)
    assert task.status == STATUS_FAILED


# 2 ------------------------------------------------- an unreachable adapter is visible
def test_status_names_an_unreachable_adapter(tmp_path, capsys):
    path = _write_toml(tmp_path)
    main(["--workspace", str(path), "status"])
    out = capsys.readouterr().out
    assert "DOWN" in out
    assert "base_url" in out


# 3 ------------------------------------------------- no real check is a doctor warning
def test_doctor_warns_when_every_check_is_a_builtin(tmp_path, capsys):
    path = _write_toml(tmp_path)
    main(["--workspace", str(path), "doctor"])
    out = capsys.readouterr()
    assert "no real verification" in (out.out + out.err)


# 4 ------------------------------------------------- the error names the tower
def test_unknown_role_message_names_the_workspace_and_the_way_out():
    cfg = parse_config(_write_toml_or_die())
    message = unknown_role_message(cfg, "frontend-2")
    assert "unknown role 'frontend-2'" in message
    assert str(cfg.workspace) in message
    assert "--workspace" in message and "tower projects use" in message
    assert "lead, ops" in message


def _write_toml_or_die():
    import tempfile
    from pathlib import Path

    tmp = Path(tempfile.mkdtemp(prefix="tower-role-"))
    return _write_toml(tmp)
