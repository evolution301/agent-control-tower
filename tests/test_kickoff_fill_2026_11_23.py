"""Pins for T-0002: the kickoff fills its own package instead of handing back a blank.

The v3 spec named the failure — a human typing YAML after the meeting — so these
tests pin the two exits: a discussion transcript, and a hand-authored package
file that `import_package` adopts only when every agreement is answered.
"""

from __future__ import annotations

import json

import pytest

from control_tower import groups, kickoff
from control_tower.board import Board
from control_tower.config import parse_config


ROLES = [
    "roles:",
    "  core:",
    '    description: "core"',
    "    adapter: mock",
    "  cli:",
    '    description: "cli"',
    "    adapter: mock",
    "  qa:",
    '    description: "qa"',
    "    adapter: mock",
]

MINUTES = """# Huddle H-0007 — fill the kickoff package

Roles: core, cli, qa · rounds: 2 · created_at: 2026-11-23T15:00:00+08:00

## Plan

best: fill the package from the huddle minutes and accept a hand-authored JSON via import_package
alternatives: keep the blank template; ask a human to edit YAML
why: the v3 spec forbids pushing the YAML back to a human

## Assignments

- core: implement fill_from_minutes and import_package in src/control_tower/kickoff.py (scope: src/control_tower/kickoff.py, tests/test_kickoff_fill_2026_11_23.py)
- cli: wire `tower kickoff --package` through src/control_tower/cli.py (scope: src/control_tower/cli.py)
- qa: run the suite and review the draft flag (scope: tests/test_kickoff_fill_2026_11_23.py)

## Acceptance

commands: python -m pytest -q
thresholds: suite green; 0 failures

## Merge order

cli → core → qa
"""

ONLY_PLAN = """# Huddle H-0008 — plan only

Roles: core, cli, qa · rounds: 2 · created_at: 2026-11-23T15:05:00+08:00

## Plan

best: fill from minutes
why: best effort beats a blank template
"""

ROUNDS = """# Huddle H-0009 — raw huddle record

Roles: core, cli, qa · rounds: 2 · created_at: 2026-11-23T15:10:00+08:00

## Round 1

### core  (exit 0)

**Position** — fill from minutes

### qa  (exit 0)

**Position** — review it

## Round 2

### core  (exit 0)

**Decision** — fill from minutes because a blank package is the named failure.

**Commitments**

- implement fill_from_minutes in src/control_tower/kickoff.py

### qa  (exit 0)

**Decision** — review the draft flag

**Commitments**

- run python -m pytest -q
"""


def _tower(tmp_path):
    workspace = tmp_path / "tower"
    workspace.mkdir()
    path = tmp_path / "tower.toml"
    lines = ["name: fill", f"workspace: {workspace.as_posix()}"] + ROLES
    lines += ["adapters:", "  dsh:", '    base_url: "http://127.0.0.1:1"']
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return parse_config(path)


def _package(cfg, group_id="G-0001"):
    return kickoff.template(cfg, group_id, "fill the package", "fill it", ["core", "cli", "qa"])


def _huddle_writing(tmp_path, text):
    path = tmp_path / "minutes.md"
    path.write_text(text, encoding="utf-8")

    def fake(cfg, board, roles, topic, rounds=2, max_roles=6):
        return {"id": "H-0007", "roles": list(roles), "path": str(path)}

    return fake


def _ledger(cfg):
    return [json.loads(line) for line in cfg.ledger_path.read_text(encoding="utf-8").splitlines()]


def test_transcript_with_the_four_sections_fills_the_package(tmp_path):
    package = _package(_tower(tmp_path))

    filled = kickoff.fill_from_minutes(package, MINUTES)

    assert package["plan"] == {
        "best": "fill the package from the huddle minutes and accept a hand-authored JSON via import_package",
        "alternatives": ["keep the blank template", "ask a human to edit YAML"],
        "why": "the v3 spec forbids pushing the YAML back to a human",
    }
    by_role = {item["role"]: item for item in package["assignments"]}
    assert by_role["core"]["work"] == "implement fill_from_minutes and import_package in src/control_tower/kickoff.py"
    assert by_role["core"]["scope"] == [
        "src/control_tower/kickoff.py",
        "tests/test_kickoff_fill_2026_11_23.py",
    ]
    assert by_role["cli"]["work"] == "wire `tower kickoff --package` through src/control_tower/cli.py"
    assert by_role["cli"]["scope"] == ["src/control_tower/cli.py"]
    assert by_role["qa"]["work"] == "run the suite and review the draft flag"
    assert by_role["qa"]["scope"] == ["tests/test_kickoff_fill_2026_11_23.py"]
    assert package["acceptance"] == {
        "commands": ["python -m pytest -q"],
        "thresholds": ["suite green; 0 failures"],
    }
    assert package["merge_order"] == ["cli", "core", "qa"]
    assert {
        "plan.best",
        "plan.alternatives",
        "plan.why",
        "assignments.core.work",
        "assignments.core.scope",
        "assignments.cli.work",
        "assignments.cli.scope",
        "assignments.qa.work",
        "assignments.qa.scope",
        "acceptance.commands",
        "acceptance.thresholds",
        "merge_order",
    } <= set(filled)
    assert kickoff.missing(package) == []


def test_a_raw_huddle_record_fills_from_decisions_and_commitments(tmp_path):
    package = _package(_tower(tmp_path))

    filled = kickoff.fill_from_minutes(package, ROUNDS)

    assert package["plan"]["best"] == "fill from minutes"
    assert package["plan"]["why"] == "a blank package is the named failure."
    by_role = {item["role"]: item for item in package["assignments"]}
    assert by_role["core"]["work"] == "implement fill_from_minutes in src/control_tower/kickoff.py"
    assert by_role["core"]["scope"] == ["src/control_tower/kickoff.py"]
    assert package["acceptance"]["commands"] == ["run python -m pytest -q"]
    assert "assignments.core.work" in filled


def test_kickoff_fills_from_the_huddle_and_logs_it(tmp_path, monkeypatch):
    cfg = _tower(tmp_path)
    monkeypatch.setattr("control_tower.huddle.huddle", _huddle_writing(tmp_path, MINUTES))

    result = kickoff.kickoff(cfg, Board(cfg), "fill the package from the huddle", ["core", "cli", "qa"], discuss=True)

    saved = kickoff.load_package(cfg, result["group"]["id"])
    assert saved["draft"] is False
    assert saved["huddle"] == "H-0007"
    assert saved["minutes"].endswith("minutes.md")
    assert result["problems"] == []
    filled = [row for row in _ledger(cfg) if row["event"] == "kickoff_filled"]
    assert filled and "acceptance.commands" in filled[-1]["fields"]
    assert filled[-1]["draft"] is False


def test_plan_only_transcript_leaves_acceptance_empty_and_draft_true(tmp_path, monkeypatch):
    cfg = _tower(tmp_path)
    monkeypatch.setattr("control_tower.huddle.huddle", _huddle_writing(tmp_path, ONLY_PLAN))

    result = kickoff.kickoff(cfg, Board(cfg), "fill only the plan", ["core", "cli", "qa"], discuss=True)

    saved = kickoff.load_package(cfg, result["group"]["id"])
    assert saved["plan"]["best"] == "fill from minutes"
    assert saved["acceptance"] == {"commands": [], "thresholds": []}
    assert all(not item["work"] for item in saved["assignments"])
    assert saved["draft"] is True
    assert any("acceptance" in problem for problem in result["problems"])


def test_import_package_rejects_an_incomplete_file(tmp_path):
    cfg = _tower(tmp_path)
    group = groups.create(cfg, "import room", ["core", "cli", "qa"])
    path = tmp_path / "incomplete.json"
    path.write_text(
        json.dumps({"plan": {"best": ""}, "assignments": [], "acceptance": {}, "merge_order": []}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as excinfo:
        kickoff.import_package(cfg, group.id, path)

    message = str(excinfo.value)
    assert "plan.best" in message
    assert "assignments[].work" in message
    assert "acceptance" in message
    assert "merge_order" in message
    assert kickoff.load_package(cfg, group.id) is None


def test_import_package_accepts_a_complete_file(tmp_path):
    cfg = _tower(tmp_path)
    group = groups.create(cfg, "import room", ["core", "cli", "qa"])
    package = _package(cfg, group.id)
    kickoff.fill_from_minutes(package, MINUTES)
    path = tmp_path / "complete.json"
    path.write_text(json.dumps(package), encoding="utf-8")

    imported = kickoff.import_package(cfg, group.id, path)

    assert imported["draft"] is False
    saved = kickoff.load_package(cfg, group.id)
    assert saved["id"] == package["id"]
    assert saved["draft"] is False
    assert kickoff.validate(saved) == []
    assert groups.load(cfg, group.id).package == package["id"]
