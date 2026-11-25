"""A present role that answers with its task report still has a position.

H-0001 (2026-11-24) is the live failure: cli answered the round-1 prompt with a
full `## Status / ## Summary / ## Files / ## Checks` report, `parse_position`
looked only for `## Position`, and the package stayed `(none)`.
"""

import json

from control_tower import huddle, kickoff

CLI_ANSWER = """## Status
ready

## Summary
A qa verdict has to be a gate in the accept path, not a document nobody reads.
I would check `task.review` inside `cmd_accept`, refuse with the exact command
to run, and record `--no-review` as `review.waived_by` on the task.

## Files
- src/control_tower/cli.py — accept gate
- src/control_tower/verify.py — shared gate helper

## Checks
- python -m pytest -q → 420 passed

## Open issues
- who is allowed to waive a verdict without a human present?

## Self-review
no findings — checks re-run
"""


def _package():
    return {
        "plan": {"best": "", "alternatives": [], "why": ""},
        "assignments": [
            {"role": "cli", "work": "", "scope": []},
            {"role": "runtime", "work": "", "scope": []},
            {"role": "core", "work": "", "scope": []},
        ],
        "acceptance": {"commands": [], "thresholds": []},
        "merge_order": ["cli", "runtime", "core"],
    }


def test_parse_position_falls_back_to_summary_and_open_issues():
    parsed = huddle.parse_position(CLI_ANSWER)
    assert parsed["position"].startswith("A qa verdict has to be a gate")
    assert parsed["questions"] == ["who is allowed to waive a verdict without a human present?"]


def test_parse_position_prefers_the_huddle_schema():
    text = "## Position\nThe short version.\n\n## Questions\n- none\n\n## Summary\nThe report version."
    parsed = huddle.parse_position(text)
    assert parsed["position"] == "The short version."
    assert parsed["questions"] == []


def test_h0001_replay_fills_plan_best_from_round_one():
    record = {
        "id": "H-0001",
        "topic": "0.18.5 acceptance: every accepted task must carry a qa verdict before release",
        "roles": ["cli", "runtime", "core"],
        "created_at": "2026-11-24T14:39:34+0800",
        "rounds": 1,
        "timeout_s": 90,
        "turns": [
            {"round": 1, "role": "cli", "exit_code": 0, "absent": False,
             "position": "", "questions": [], "answer": CLI_ANSWER},
            {"round": 1, "role": "runtime", "exit_code": -1, "absent": True,
             "position": "", "questions": []},
            {"round": 1, "role": "core", "exit_code": -1, "absent": True,
             "position": "", "questions": []},
        ],
    }
    minutes = huddle.render_markdown(record)
    assert "A qa verdict has to be a gate" in minutes

    package = _package()
    filled = kickoff.fill_from_minutes(package, minutes)
    assert "plan.best" in filled
    assert package["plan"]["best"].startswith("A qa verdict has to be a gate")


def test_huddle_collects_the_report_style_answer(tmp_path, monkeypatch):
    from control_tower.adapters.base import AdapterResult
    from control_tower.board import Board
    from control_tower.config import ensure_layout, parse_config

    class FakeAdapter:
        def run(self, req):
            return AdapterResult(exit_code=0, text=CLI_ANSWER)

    monkeypatch.setattr("control_tower.converse.get_adapter",
                        lambda name, options: FakeAdapter())
    cfg_path = tmp_path / "tower.json"
    cfg_path.write_text(json.dumps({
        "name": "huddle-fallback",
        "workspace": str(tmp_path),
        "roles": {"cli": {"adapter": "mock"}, "core": {"adapter": "mock"}},
        "huddle": {"timeout_s": 5},
    }), encoding="utf-8")
    cfg = parse_config(cfg_path)
    ensure_layout(cfg)

    record = huddle.huddle(cfg, Board(cfg), ["cli", "core"], "qa gate", rounds=1)
    cli_turn = next(t for t in record["turns"] if t["role"] == "cli")
    assert cli_turn["position"].startswith("A qa verdict has to be a gate")
    assert cli_turn["answer"].startswith("## Status")
