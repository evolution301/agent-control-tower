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
    """Mirrors the adapter contract: session_pressure returns pct (0-100)."""

    def __init__(self, pressures):
        self.pressures = pressures          # session id -> pct
        self.asked = []

    def session_pressure(self, session_id):
        self.asked.append(session_id)
        if session_id in self.pressures:
            return {"session": session_id, "pct": self.pressures[session_id]}
        return None


def test_tower_ctx_prints_pressure_and_flags_the_rule(tower, monkeypatch, capsys):
    fake = FakeAdapter({"session-abc-1234-5678-9012": 62})
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


def test_adapter_session_pressure_math_desktop_vs_headless(monkeypatch):
    """John's real numbers: same ratio math across different context windows."""
    from control_tower.adapters.dsh import DshAdapter

    payloads = {
        "session-desktop": {"projections": {"values": {"contextPressure": {
            "pressureTokens": 835461, "projectedTokens": 835461,
            "contextWindow": 1048576}}}},                       # desktop 1Mi
        "session-headless": {"projections": {"values": {"contextPressure": {
            "pressureTokens": 118120, "projectedTokens": 118120,
            "contextWindow": 262144}}}},                        # headless 256Ki
    }
    adapter = object.__new__(DshAdapter)
    monkeypatch.setattr(adapter, "rpc",
                        lambda method, params, timeout=0: {"result": payloads[params["request"]["sessionId"]]})
    monkeypatch.setattr(adapter, "_result_value", lambda reply: reply.get("result"))

    desktop = adapter.session_pressure("session-desktop")
    headless = adapter.session_pressure("session-headless")
    assert desktop["pct"] == round(835461 / 1048576 * 100, 2)
    assert headless["pct"] == round(118120 / 262144 * 100, 2)
    assert desktop["contextWindow"] == 1048576 and headless["contextWindow"] == 262144
