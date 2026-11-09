"""Regression tests: relay exact session-id + plugin inject declaration."""
import json

import pytest

from control_tower.config import ensure_layout, parse_config
from control_tower import cli
from control_tower.relay import resolve_target


@pytest.fixture()
def tower(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = {
        "name": "relay-id-tower",
        "workspace": str(tmp_path),
        "roles": {"seo": {"description": "s", "adapter": "mock"}},
    }
    path = tmp_path / "tower.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    cfg = parse_config(path)
    ensure_layout(cfg)
    return cfg


def test_an_exact_session_id_never_falls_back_to_title_match(tower):
    """John's repro: a session- prefixed id was treated as a title substring."""

    class NoTitleSearch:
        def find_session_by_title(self, title, cwd=""):
            raise AssertionError("title search must not run for exact ids")

    session = "session-4323654f-59d8-4be8-8ef0-443aa9182587"
    session_id, how = resolve_target(tower, NoTitleSearch(), session)
    assert session_id == session and how == "session-id"


def test_a_bare_uuid_is_also_an_exact_id(tower):
    bare = "4323654f-59d8-4be8-8ef0-443aa9182587"
    session_id, how = resolve_target(tower, _Noop(), bare)
    assert session_id == bare and how == "session-id"


class _Noop:
    def find_session_by_title(self, title, cwd=""):
        return None


def test_plugin_declares_agents_inject_and_compaction_stays_optional():
    """agents is injected (available), compaction stays optional so the plugin
    never pends when dsh-compaction-basic is not loaded."""
    import pathlib

    src = pathlib.Path("packages/dsh-tower/index.js").read_text(encoding="utf-8")
    assert "export const inject = ['tools', 'agents']" in src
    assert "compaction" not in src.split("export const inject")[1].split("\n")[0]


def test_relay_accepts_both_to_forms_and_contract_matches_the_cli(tower, capsys):
    """B17: the contract told peers `--to`, the CLI only took a positional."""
    from control_tower.relay import RELAY_CONTRACT

    # the contract now teaches the positional form (and names --to as alias)
    assert "tower relay tower --reply" in RELAY_CONTRACT
    assert "--to tower --reply" in RELAY_CONTRACT, "alias documented"

    assert cli.main(["relay", "--to", "tower", "-m", "via --to"]) == 0
    out = capsys.readouterr().out
    assert "delivered" in out
    assert cli.main(["relay", "tower", "-m", "via positional"]) == 0
    out = capsys.readouterr().out
    assert "delivered" in out
    assert cli.main(["relay", "-m", "missing target"]) == 2
