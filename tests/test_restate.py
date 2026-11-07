"""The restatement gate (v0.8, playbook point 7).

All offline: the mock adapter answers the restate prompt deterministically, and
the failure paths swap in a stub adapter so "fails closed" is tested rather than
assumed.
"""

import json
import sys

import pytest

from control_tower import cli
from control_tower.adapters import AdapterResult
from control_tower.board import Board, STATUS_REPORTED, STATUS_RESTATING, STATUS_FAILED
from control_tower.config import ensure_layout, parse_config
from control_tower.dispatch import parse_restatement


@pytest.fixture()
def tower(tmp_path, monkeypatch):
    config = {
        "name": "test-tower",
        "workspace": str(tmp_path),
        "roles": {
            "backend": {
                "description": "server side",
                "adapter": "mock",
                "write_scopes": ["roles/backend/"],
                "max_rounds": 2,
            },
        },
        "verify_commands": [f'"{sys.executable}" -c "print(1)"'],
    }
    path = tmp_path / "tower.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    cfg = parse_config(path)
    ensure_layout(cfg)
    monkeypatch.chdir(tmp_path)
    return cfg


def _events(cfg):
    lines = cfg.ledger_path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line)["event"] for line in lines]


class _StubAdapter:
    """An adapter whose answer is fixed, so the gate's failure paths are testable."""

    name = "stub"

    def __init__(self, text="", exit_code=0):
        self.text = text
        self.exit_code = exit_code

    def run(self, req):  # noqa: ANN001, ANN201 - test double
        return AdapterResult(text=self.text, exit_code=self.exit_code, meta={})


def _use_stub(monkeypatch, text="", exit_code=0):
    stub = _StubAdapter(text=text, exit_code=exit_code)
    monkeypatch.setattr("control_tower.dispatch.get_adapter", lambda *a, **k: stub)
    return stub


# ------------------------------------------------------------------ the gate


def test_dispatch_restate_records_a_restatement_and_runs_no_work(tower, capsys):
    rc = cli.main(["dispatch", "backend", "build the settings page", "--restate"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "restatement" in out.lower()

    task = Board(tower).load("T-0001")
    assert task.status == STATUS_RESTATING
    assert task.restatement, "the restatement must be stored on the task"
    assert task.restate_ok is False
    assert task.report is None, "no report may exist before the gate is opened"
    assert task.attention and "Restatement" in task.attention["question"]

    # The mock writes an artifact only on a work turn: its absence proves that
    # `dispatch --restate` dispatched no work.
    role_dir = tower.workspace / "roles" / "backend"
    assert not list(role_dir.glob("*-MOCK-ARTIFACT.md"))
    assert "restatement" in _events(tower)


def test_a_restating_task_needs_a_person(tower):
    cli.main(["dispatch", "backend", "needs a decision", "--restate"])
    ids = [t.id for t in Board(tower).needs_person()]
    assert "T-0001" in ids


def test_restate_ok_dispatches_the_work_with_the_confirmed_understanding(tower, capsys):
    cli.main(["dispatch", "backend", "build the settings page", "--restate"])
    capsys.readouterr()

    rc = cli.main(["restate", "ok", "T-0001"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "restatement accepted" in out

    task = Board(tower).load("T-0001")
    assert task.status == STATUS_REPORTED
    assert task.report and task.report["summary"]
    assert task.restate_ok is True
    assert task.attention is None

    # The work prompt must carry the accepted restatement, so the executor works
    # from the text a person actually approved.
    prompt = (tower.task_dir("T-0001") / "prompt.md").read_text(encoding="utf-8")
    assert "## Confirmed understanding" in prompt
    assert task.restatement.splitlines()[0][:40] in prompt

    role_dir = tower.workspace / "roles" / "backend"
    assert list(role_dir.glob("*-MOCK-ARTIFACT.md")), "the work turn must run after the gate"
    assert "restatement_accepted" in _events(tower)


def test_restate_ok_json_is_a_single_document(tower, capsys):
    cli.main(["dispatch", "backend", "json restate", "--restate", "--json"])
    capsys.readouterr()
    rc = cli.main(["restate", "ok", "T-0001", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)
    assert data["id"] == "T-0001"
    assert data["restate_ok"] is True


# ------------------------------------------------------- the gate fails closed


def test_restate_gate_fails_closed_without_a_restatement_section(tower, monkeypatch):
    """A role that answers with a normal report understood nothing yet."""
    _use_stub(
        monkeypatch,
        text="## Status\nready\n\n## Summary\nI just started working.\n\n## Files\n- none\n\n## Checks\n- none\n",
    )
    rc = cli.main(["dispatch", "backend", "ambiguous brief", "--restate"])
    assert rc == 0
    task = Board(tower).load("T-0001")
    assert task.status == STATUS_FAILED
    assert not task.restatement
    assert task.attention is None
    assert "T-0001" not in [t.id for t in Board(tower).needs_person()]


def test_restate_gate_fails_closed_on_an_adapter_error(tower, monkeypatch):
    _use_stub(monkeypatch, text="boom", exit_code=1)
    cli.main(["dispatch", "backend", "will fail", "--restate"])
    task = Board(tower).load("T-0001")
    assert task.status == STATUS_FAILED
    assert not task.restatement


def test_restate_ok_refuses_a_task_that_is_not_restating(tower, capsys):
    cli.main(["dispatch", "backend", "normal dispatch"])
    capsys.readouterr()
    rc = cli.main(["restate", "ok", "T-0001"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "not waiting for a restatement" in (captured.out + captured.err)


def test_accept_refuses_a_restating_task_and_leaves_it_unchanged(tower, capsys):
    """There is no report to accept: `accept` must not become a shortcut past the gate."""
    cli.main(["dispatch", "backend", "no work yet", "--restate"])
    capsys.readouterr()
    rc = cli.main(["accept", "T-0001"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "restate ok" in (captured.out + captured.err)
    task = Board(tower).load("T-0001")
    assert task.status == STATUS_RESTATING


# ------------------------------------------------------------------ the parser


def test_parse_restatement_reads_the_three_sections():
    text = """## Restatement
I will build the settings page and wire it to the existing config.

## Assumptions
- "settings" means the profile page
- none

## Done looks like
The page loads and the config round-trips.
"""
    parsed = parse_restatement(text)
    assert parsed["restatement"].startswith("I will build the settings page")
    assert parsed["assumptions"] == ['"settings" means the profile page']
    assert parsed["done"] == "The page loads and the config round-trips."


def test_parse_restatement_is_empty_on_a_report_without_the_section():
    parsed = parse_restatement("## Summary\nDid the work.\n")
    assert parsed["restatement"] == ""
    assert parsed["assumptions"] == []
