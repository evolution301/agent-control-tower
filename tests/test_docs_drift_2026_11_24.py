"""Docs drift guards and the doctor's review-role warning."""

import json
from pathlib import Path

from control_tower import cli
from control_tower.config import ensure_layout, parse_config

ROOT = Path(__file__).resolve().parents[1]


def _text(*parts: str) -> str:
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def test_readmes_do_not_hardcode_test_counts():
    for name in ("README.md", "README.zh.md"):
        text = _text(name)
        assert "299%20python" not in text
        assert "299 python" not in text
        assert "31%20node" not in text


def test_docs_do_not_understate_the_tool_count():
    for name in ("README.md", "README.zh.md"):
        text = _text(name)
        assert "five tools" not in text
        assert "5 个工具" not in text
    assert "five tools" not in _text("packages", "dsh-tower", "cordis.patch.yml")
    assert "19 tools" in _text("README.md")
    assert "19 个工具" in _text("README.zh.md")


def test_architecture_doc_describes_the_thread_pool():
    text = _text("docs", "architecture.md")
    assert "thread pool" in text
    assert "including the same role" in text
    assert "`serial_requests: true` opts into a per-role lock" in text
    assert "one role never runs two adapter turns" not in text
    assert "Default is sequential" not in text
    assert "lock manager" not in text


def test_adapter_docs_wait_on_the_report_file():
    assert "polls `session/list`" not in _text("docs", "dsh-quickstart.md")
    assert "polls the report file" in _text("docs", "dsh-quickstart.md")
    assert "polls `session/list`" not in _text("docs", "adapters.md")
    assert "polls that file" in _text("docs", "adapters.md")


def test_person_states_do_not_include_ready():
    text = _text("docs", "superagent-ideas.md")
    assert "`ready` / `blocked` / `failed`" not in text
    assert "(`blocked` / `failed`)" in text


def _write_tower(root: Path, roles: dict) -> None:
    config = {"name": "doctor-test", "workspace": str(root), "roles": roles}
    path = root / "tower.toml"
    path.write_text(json.dumps(config), encoding="utf-8")
    cfg = parse_config(path)
    ensure_layout(cfg)


def _roles() -> dict:
    return {
        "backend": {"description": "server", "adapter": "mock", "write_scopes": ["roles/backend/"]},
        "qa": {"description": "verify", "adapter": "mock", "write_scopes": []},
    }


def test_doctor_warns_when_the_review_role_is_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TOWER_HOME", str(tmp_path / "home"))
    _write_tower(tmp_path, _roles())
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert ("adversarial review never runs: review role 'auditor' is not a role here — "
            "add it or set review_roles") in out

    roles = _roles()
    roles["auditor"] = {"description": "review", "adapter": "mock", "write_scopes": []}
    (tmp_path / "tower.toml").unlink()
    _write_tower(tmp_path, roles)
    assert cli.main(["doctor"]) == 0
    assert "adversarial review never runs" not in capsys.readouterr().out
