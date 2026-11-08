"""Regression tests: relay exact session-id + plugin inject declaration."""
import json

import pytest

from control_tower.config import ensure_layout, parse_config
from control_tower.relay import resolve_target


@pytest.fixture()
def tower(tmp_path):
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


def test_plugin_declares_agents_and_compaction_inject():
    """The compact tool reads ctx.agents/ctx.compaction — they must be injected."""
    src = (pytest.pathlib.Path("packages/dsh-tower/index.js")
           if hasattr(pytest, "pathlib") else None)
    import pathlib

    src = pathlib.Path("packages/dsh-tower/index.js").read_text(encoding="utf-8")
    assert "export const inject = ['tools', 'agents', 'compaction']" in src
