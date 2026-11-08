"""Regression tests for the 2026-11-09 tower fixes.

Each test pins a failure that cost real time in production:

1. `tower doctor` said `problems: none` while `base_url` pointed at a closed port
   and `secret` was stale — the first symptom was a dispatch that did not fail
   until the wait_s timeout.
2. `verify_commands` ran with `cwd = workspace`, so a relative `node
   scripts/lint.mjs` was `MODULE_NOT_FOUND` and *every* accept failed.
3. Reported files were resolved only against the workspace, so a deliverable
   written into the project root was reported missing … and a wrong-root write
   into the workspace passed.
4. `session_pressure` called `session/projections` with the wrong args
   (`sessionId` instead of `{"request": {"sessionId"}}`) and always returned None.
5. A session with no model selected accepted the prompt and then never started a
   turn: the adapter burned `wait_s` (900s) and reported a bare timeout.
"""

from __future__ import annotations

import sys
from pathlib import Path

from control_tower.adapters import AdapterRequest, get_adapter
from control_tower.board import Task
from control_tower.cli import _dsh_preflight
from control_tower.config import parse_config
from control_tower.verify import verify_report


def _write_toml(tmp_path: Path, *, base_url: str, verify_cwd: str = "", project_root: str = "") -> Path:
    workspace = tmp_path / "tower"
    workspace.mkdir(exist_ok=True)
    toml = [
        "name: patchtest",
        f"workspace: {workspace.as_posix()}",
    ]
    if project_root:
        toml.append(f"project_root: {project_root}")
    if verify_cwd:
        toml.append(f"verify_cwd: {verify_cwd}")
    toml += [
        "roles:",
        "  copy:",
        '    description: "test role"',
        "    adapter: dsh",
        '    write_scopes: [".agents/workspace/copy/"]',
        "adapters:",
        "  dsh:",
        f'    base_url: "{base_url}"',
        '    secret: "pML0al_gZwed9fBYAyvlv5gtM-PxKRcIQSY3W7IJkS0"',
        "    sessions:",
        '      copy: "session-00000000-0000-0000-0000-000000000000"',
        "    wait_s: 5",
        "    poll_s: 0.2",
        "",
    ]
    path = tmp_path / "tower.toml"
    path.write_text("\n".join(toml), encoding="utf-8")
    return path


def _report(files: list[str]) -> dict:
    return {
        "state": "ready",
        "summary": "test",
        "self_review": "checked",
        "files": files,
        "checks": ["cmd → result"],
    }


# 1 ---------------------------------------------------------------------------
def test_doctor_flags_a_dead_adapter_endpoint(tmp_path):
    cfg = parse_config(_write_toml(tmp_path, base_url="http://127.0.0.1:1"))
    problems = _dsh_preflight(cfg)
    assert any("unreachable or unauthorized" in p for p in problems), problems


def test_doctor_is_quiet_when_the_endpoint_answers(tmp_path, monkeypatch):
    cfg = parse_config(_write_toml(tmp_path, base_url="http://127.0.0.1:61290"))
    adapter = get_adapter("dsh", cfg.adapter_options["dsh"])
    monkeypatch.setattr(adapter.__class__, "rpc", lambda self, m, a, timeout=15: {"result": {"ok": True, "value": {"items": [
        {"sessionId": "session-00000000-0000-0000-0000-000000000000"},
    ]}}})
    monkeypatch.setattr("control_tower.adapters.get_adapter", lambda name, opts: adapter)
    assert _dsh_preflight(cfg) == []


# 2 / 3 -----------------------------------------------------------------------
def test_verify_runs_relative_command_from_verify_cwd(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "check.py").write_text("print('ok')\n", encoding="utf-8")
    cfg = parse_config(_write_toml(tmp_path, base_url="http://127.0.0.1:1", verify_cwd=str(project)))
    cfg.verify_commands = [f'"{sys.executable}" check.py']
    task = Task(id="T-TEST", role="copy", brief="x")
    task.report = _report(["docs/FILE.md"])
    (project / "docs").mkdir()
    (project / "docs" / "FILE.md").write_text("x", encoding="utf-8")

    result = verify_report(cfg, task)
    assert result.ran and result.ran[0]["exit_code"] == 0, result.ran
    assert Path(result.ran[0]["cwd"]) == project
    assert not any("do not exist" in p for p in result.problems), result.problems


def test_verify_resolves_reported_files_in_project_root(tmp_path):
    project = tmp_path / "project"
    (project / "docs").mkdir(parents=True)
    (project / "docs" / "FILE.md").write_text("x", encoding="utf-8")
    cfg = parse_config(_write_toml(tmp_path, base_url="http://127.0.0.1:1", project_root=str(project)))
    task = Task(id="T-TEST", role="copy", brief="x")
    task.report = _report(["docs/FILE.md"])
    assert not any("do not exist" in p for p in verify_report(cfg, task).problems)


def test_verify_still_flags_a_missing_file(tmp_path):
    cfg = parse_config(_write_toml(tmp_path, base_url="http://127.0.0.1:1"))
    task = Task(id="T-TEST", role="copy", brief="x")
    task.report = _report(["docs/NOPE.md"])
    problems = verify_report(cfg, task).problems
    assert any("do not exist" in p for p in problems), problems


# 4 ---------------------------------------------------------------------------
def test_session_pressure_reads_the_projections_envelope():
    adapter = get_adapter("dsh", {"base_url": "http://127.0.0.1:1"})
    seen = {}

    def fake_rpc(method, args, timeout=90):
        seen["method"], seen["args"] = method, args
        return {"result": {"ok": True, "value": {"values": {
            "contextPressure": {"pressureTokens": 1234, "contextWindow": 100000},
            "sessionStats": {"turns": 2, "llmMs": 5000},
            "modelSelection": {"lastUsed": {"provider": "p", "model": "m"}},
        }}}}

    adapter.rpc = fake_rpc  # type: ignore[assignment]
    pressure = adapter.session_pressure("s1")
    # v0.10.6 contract: the object is converted to percent + raw fields kept
    assert pressure == {"session": "s1", "pct": 1.23,
                        "pressureTokens": 1234, "contextWindow": 100000}
    assert seen["args"] in ({"sessionId": "s1"}, {"request": {"sessionId": "s1"}}), seen["args"]
    assert adapter.session_state("s1") == {"turns": 2, "llmMs": 5000, "steps": 0,
                                           "model": "m", "provider": "p", "model_known": True}


# 5 ---------------------------------------------------------------------------
def test_run_fails_fast_when_the_session_has_no_model(tmp_path):
    adapter = get_adapter("dsh", {
        "base_url": "http://127.0.0.1:1",
        "sessions": {"copy": "session-00000000-0000-0000-0000-000000000000"},
        "wait_s": 30,
        "poll_s": 0.05,
        "turn_start_s": 0.3,
    })
    adapter.rpc = lambda method, args, timeout=90: {"result": {"ok": True}}  # type: ignore[assignment]
    adapter.session_state = lambda sid: {"turns": 0, "llmMs": 0, "model": None, "model_known": True}  # type: ignore[assignment]
    req = AdapterRequest(role="copy", prompt="hi", cwd=tmp_path, task_id="T-TEST")

    result = adapter.run(req)

    assert result.exit_code == 4
    assert "没有可用模型" in result.text
    assert result.meta and result.meta.get("cold_session") is True


def test_verify_expands_a_brace_pattern_in_reported_files(tmp_path):
    project = tmp_path / "project"
    shots = project / ".agents" / "workspace" / "ui" / "shots"
    shots.mkdir(parents=True)
    for name in ("home-1440.png", "home-390.png"):
        (shots / name).write_text("x", encoding="utf-8")
    cfg = parse_config(_write_toml(tmp_path, base_url="http://127.0.0.1:1", project_root=str(project)))
    task = Task(id="T-TEST", role="copy", brief="x")
    task.report = _report([".agents/workspace/ui/shots/home-{1440,390}.png"])
    assert not any("do not exist" in p for p in verify_report(cfg, task).problems)

    task.report["files"] = [".agents/workspace/ui/shots/{home,faq}-1440.png"]
    assert any("do not exist" in p for p in verify_report(cfg, task).problems)


def test_verify_does_not_fake_a_missing_file_with_braces(tmp_path):
    cfg = parse_config(_write_toml(tmp_path, base_url="http://127.0.0.1:1"))
    task = Task(id="T-TEST", role="copy", brief="x")
    task.report = _report(["docs/{A,B}.md"])
    assert any("do not exist" in p for p in verify_report(cfg, task).problems)
