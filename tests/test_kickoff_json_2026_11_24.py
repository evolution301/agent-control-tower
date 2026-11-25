"""`kickoff --discuss --json` must expose the discuss summary at the top level.

T-0031 nested it only under `package.discuss`; the field report and R-0108 P0-B
read `json.discuss.state` from the printed document.
"""

import json
import time

from control_tower import cli
from control_tower.adapters.base import AdapterResult
from control_tower.config import ensure_layout, parse_config


def _tower(tmp_path, roles, timeout_s=1):
    workspace = tmp_path / "tower"
    workspace.mkdir(exist_ok=True)
    path = tmp_path / "tower.json"
    path.write_text(json.dumps({
        "name": "kickoff-json",
        "workspace": str(workspace),
        "roles": {name: {"description": name, "adapter": "mock"} for name in roles},
        "huddle": {"timeout_s": timeout_s},
    }), encoding="utf-8")
    cfg = parse_config(path)
    ensure_layout(cfg)
    return cfg


def _fake_adapter(monkeypatch, slow):
    class FakeAdapter:
        def run(self, req):
            if req.role in slow:
                time.sleep(5)
            text = (
                "## Position\nShip the smallest fix and keep the diff local.\n\n"
                "## Decision\nShip the smallest fix because it is reversible.\n\n"
                "## Commitments\n- write the failing test (scope: tests/test_fix.py)\n"
            )
            return AdapterResult(exit_code=0, text=text)

    monkeypatch.setattr("control_tower.converse.get_adapter", lambda name, options: FakeAdapter())


def _kickoff_json(tmp_path, monkeypatch, capsys, slow):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TOWER_HOME", str(tmp_path / "home"))
    _tower(tmp_path, ["fast", "slow"])
    _fake_adapter(monkeypatch, slow)
    brief = tmp_path / "brief.md"
    brief.write_text("fix the signup bug", encoding="utf-8")
    code = cli.main([
        "kickoff", "--brief-file", str(brief), "--title", "signup",
        "--role", "fast", "--role", "slow", "--discuss", "--json",
    ])
    assert code == 0
    return json.loads(capsys.readouterr().out)


def test_kickoff_json_promotes_discuss_on_partial(tmp_path, monkeypatch, capsys):
    document = _kickoff_json(tmp_path, monkeypatch, capsys, {"slow"})

    discuss = document["discuss"]
    assert set(discuss) == {"state", "absent", "seconds"}
    assert discuss["state"] == "partial"
    assert discuss["absent"] == ["slow"]
    assert isinstance(discuss["seconds"], int)
    assert document["package"]["discuss"] == discuss


def test_kickoff_json_promotes_discuss_on_timeout(tmp_path, monkeypatch, capsys):
    document = _kickoff_json(tmp_path, monkeypatch, capsys, {"fast", "slow"})

    discuss = document["discuss"]
    assert set(discuss) == {"state", "absent", "seconds"}
    assert discuss["state"] == "timeout"
    assert discuss["absent"] == ["fast", "slow"]
    assert document["package"]["discuss"] == discuss
