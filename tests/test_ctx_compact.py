"""Context-pressure visibility (tower ctx) and the plugin compact seam."""

import json
import pathlib

import pytest

from control_tower import cli
from control_tower.config import ensure_layout, parse_config


@pytest.fixture()
def tower(tmp_path, monkeypatch):
    config = {
        "name": "ctx-tower",
        "workspace": str(tmp_path),
        "roles": {"seo": {"description": "s", "adapter": "mock"}},
        "adapter_options": {
            "dsh": {"sessions": {"seo": "session-abc-1234-5678-9012"}}
        },
        "adapters": {
            "dsh": {"sessions": {"seo": "session-abc-1234-5678-9012"}}
        },
    }
    path = tmp_path / "tower.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    cfg = parse_config(path)
    ensure_layout(cfg)
    monkeypatch.chdir(tmp_path)
    return cfg


class FakeAdapter:
    def __init__(self, pressures):
        self.pressures = pressures          # session id -> pressure float
        self.asked = []

    def session_pressure(self, session_id):
        self.asked.append(session_id)
        if session_id in self.pressures:
            return {"session": session_id,
                    "contextPressure": self.pressures[session_id]}
        return None


def test_tower_ctx_prints_pressure_and_flags_the_rule(tower, monkeypatch, capsys):
    fake = FakeAdapter({"session-abc-1234-5678-9012": 0.62})
    monkeypatch.setattr("control_tower.adapters.get_adapter", lambda name, opts: fake)
    assert cli.main(["ctx"]) == 0
    out = capsys.readouterr().out
    assert "pressure=62%" in out
    assert "COMPACT NOW" in out, "the John rule is visible at the point of reading"
    assert fake.asked == ["session-abc-1234-5678-9012"]
    assert "tower_compact" in out, "the one-shot trigger is named"


def test_tower_ctx_degrades_when_projections_are_unavailable(tower, monkeypatch, capsys):
    fake = FakeAdapter({})
    monkeypatch.setattr("control_tower.adapters.get_adapter", lambda name, opts: fake)
    assert cli.main(["ctx"]) == 0
    out = capsys.readouterr().out
    assert "projections unavailable" in out
    assert "COMPACT NOW" not in out


def test_compact_error_text_matches_the_native_slack_outcomes():
    import subprocess
    import tempfile

    index_js = (pathlib.Path(__file__).resolve().parents[1]
                / "packages" / "dsh-tower" / "index.js").as_posix()
    script = pathlib.Path(tempfile.mkdtemp()) / "t.mjs"
    script.write_text(
        "import { compactErrorText } from 'file:///" + index_js + "';\n"
        "const cases = ['busy', 'summary', 'commit', 'persistence'];\n"
        "for (const c of cases) console.log(c + ':' + compactErrorText(c));\n",
        encoding="utf-8",
    )
    out = subprocess.run(["node", str(script)], capture_output=True, text=True,
                         shell=True, timeout=30)
    assert "busy:Compaction is unavailable" in out.stdout
    assert "persistence:" in out.stdout
