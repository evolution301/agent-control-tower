"""Session pool: spare DSH sessions created and registered before boot (T-0050).

DSH reads its sidebar registry (`$DSH_HOME/storages/workspace.json`) once per
boot, so a session created later is invisible until the app restarts. Bootstrap
therefore creates `adapters.dsh.pool_size` spare sessions and registers them;
`role add` claims one. The rule under test is reconciliation, never append: two
consecutive bootstraps create N sessions in total, and a lost or corrupt record
is rebuilt from the registry instead of duplicating the pool.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from control_tower import bootstrap
from control_tower.config import load_config


class FakeDsh:
    """Tape adapter: session/list and session/create behave like the harness."""

    def __init__(self):
        self.sessions = {"qa": "S-qa"}
        self.titles = {"S-qa": "qa"}
        self.created: list[tuple[str, str]] = []
        self.fail_list = False

    def resolve_session(self, request):
        return self.sessions.get(request.role, ""), "cache"

    def create_session(self, title, cwd=""):
        session_id = f"P-{len(self.created) + 1}"
        self.created.append((title, session_id))
        self.titles[session_id] = title
        return session_id

    def rpc(self, method, args=None, timeout=None):
        assert method == "session/list", method
        if self.fail_list:
            raise bootstrap.DshRpcError(method, "gateway/down", "harness is down")
        items = [
            {"sessionId": sid, "projections": {"values": {"title": title}}}
            for sid, title in self.titles.items()
        ]
        return {"result": {"ok": True, "value": {"items": items}}}


@pytest.fixture
def tower(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    (ws / ".tower").mkdir(parents=True)
    toml = tmp_path / "tower.toml"
    toml.write_text(
        "\n".join([
            "name: pool",
            f"workspace: {ws.as_posix()}",
            "roles:",
            "  qa:",
            "    adapter: dsh",
            "adapters:",
            "  dsh:",
            '    base_url: "http://127.0.0.1:1"',
            "    pool_size: 2",
        ]) + "\n",
        encoding="utf-8",
    )
    home = tmp_path / "dsh"
    (home / "storages").mkdir(parents=True)
    registry = home / "storages" / "workspace.json"
    registry.write_text(json.dumps({"tables": {"workspaces": {"W-1": {
        "path": str(ws), "sessionIds": ["S-qa"],
    }}}}), encoding="utf-8")
    monkeypatch.setenv("DSH_HOME", str(home))
    fake = FakeDsh()
    monkeypatch.setattr(bootstrap, "adapter_for", lambda *a, **k: fake)
    monkeypatch.setattr(bootstrap, "detect_secret", lambda workspace: "c2VjcmV0")
    monkeypatch.setattr(bootstrap, "detect_base_url", lambda **kw: ("http://127.0.0.1:9", False))
    return toml, ws, fake, registry


def _run(toml, ws):
    return bootstrap.run_bootstrap(toml, workspace=ws)


def _pool(ws):
    return json.loads((ws / ".tower" / "dsh_pool.json").read_text(encoding="utf-8"))


def _registered(registry):
    data = json.loads(registry.read_text(encoding="utf-8"))
    return data["tables"]["workspaces"]["W-1"]["sessionIds"]


def test_two_bootstraps_create_and_register_each_session_once(tower):
    toml, ws, fake, registry = tower
    first = _run(toml, ws)
    assert len(first["pool"]["created"]) == 2
    pool = _pool(ws)
    assert pool["size"] == 2
    assert len(pool["free"]) == 2
    assert pool["claimed"] == {}
    assert _registered(registry) == ["S-qa", *pool["free"]]

    second = _run(toml, ws)
    assert second["pool"]["created"] == []
    assert second["pool"]["free"] == pool["free"]
    assert _registered(registry) == ["S-qa", *pool["free"]]
    assert len(fake.created) == 2
    assert all(title.startswith(bootstrap.POOL_PREFIX) for title, _ in fake.created)


def test_pool_size_zero_keeps_todays_bootstrap(tower):
    toml, ws, fake, _registry = tower
    toml.write_text(
        toml.read_text(encoding="utf-8").replace("    pool_size: 2\n", ""), encoding="utf-8"
    )
    summary = _run(toml, ws)
    assert "pool" not in summary
    assert [step["step"] for step in summary["steps"]] == [
        "secret", "base_url", "config", "verify_commands", "sessions",
    ]
    assert not (ws / ".tower" / "dsh_pool.json").exists()
    assert fake.created == []


def test_corrupt_record_is_rebuilt_from_the_registry(tower):
    toml, ws, fake, _registry = tower
    _run(toml, ws)
    before = _pool(ws)
    (ws / ".tower" / "dsh_pool.json").write_text("{not json", encoding="utf-8")
    summary = _run(toml, ws)
    assert summary["pool"]["created"] == []
    assert _pool(ws)["free"] == before["free"]
    assert len(fake.created) == 2


def test_a_vanished_free_session_is_replaced(tower):
    toml, ws, fake, _registry = tower
    _run(toml, ws)
    gone = _pool(ws)["free"][0]
    fake.titles.pop(gone)
    summary = _run(toml, ws)
    assert len(summary["pool"]["created"]) == 1
    assert gone not in summary["pool"]["free"]
    assert len(_pool(ws)["free"]) == 2


def test_a_claimed_session_does_not_count_as_capacity(tower):
    toml, ws, _fake, _registry = tower
    _run(toml, ws)
    free = _pool(ws)["free"]
    claimed = free.pop()
    (ws / ".tower" / "dsh_pool.json").write_text(
        json.dumps({"size": 2, "free": free, "claimed": {"fe": claimed}}), encoding="utf-8"
    )
    summary = _run(toml, ws)
    assert len(summary["pool"]["created"]) == 1
    assert len(summary["pool"]["free"]) == 2
    assert summary["pool"]["claimed"] == {"fe": claimed}


def test_a_claim_written_by_the_cli_is_not_flattened(tower):
    toml, ws, _fake, _registry = tower
    _run(toml, ws)
    free = _pool(ws)["free"]
    taken = free.pop()
    entry = {"session": taken, "title": "fe"}
    (ws / ".tower" / "dsh_pool.json").write_text(
        json.dumps({"size": 2, "free": free, "claimed": {"fe": entry}}), encoding="utf-8"
    )
    summary = _run(toml, ws)
    assert len(summary["pool"]["created"]) == 1
    saved = _pool(ws)
    assert saved["claimed"] == {"fe": entry}
    assert taken not in saved["free"]


def test_free_entries_may_carry_their_title(tower):
    toml, ws, _fake, _registry = tower
    _run(toml, ws)
    free = _pool(ws)["free"]
    (ws / ".tower" / "dsh_pool.json").write_text(
        json.dumps({"size": 2, "free": [{"session": sid, "title": "spare"} for sid in free],
                    "claimed": {}}),
        encoding="utf-8",
    )
    summary = _run(toml, ws)
    assert summary["pool"]["created"] == []
    assert summary["pool"]["free"] == free


def test_no_registry_row_reports_the_reason_and_creates_nothing(tower):
    toml, ws, fake, registry = tower
    registry.write_text(json.dumps({"tables": {"workspaces": {
        "W-9": {"path": "/elsewhere", "sessionIds": []},
    }}}), encoding="utf-8")
    summary = _run(toml, ws)
    assert fake.created == []
    assert summary["pool"]["error"].startswith("this tower's workspace is not in the DSH registry")


def test_a_failed_session_list_leaves_the_record_alone(tower):
    toml, ws, fake, _registry = tower
    _run(toml, ws)
    before = _pool(ws)
    fake.fail_list = True
    summary = _run(toml, ws)
    assert "session/list failed" in summary["pool"]["error"]
    assert summary["pool"]["free"] == before["free"]
    assert _pool(ws) == before


def test_doctor_warning_only_when_no_free_session_remains(tower):
    toml, ws, _fake, _registry = tower
    cfg = load_config(toml.parent)
    assert bootstrap.pool_warning(cfg) == bootstrap.POOL_EMPTY_WARNING
    _run(toml, ws)
    assert bootstrap.pool_warning(cfg) == ""
    assert bootstrap.POOL_EMPTY_WARNING == "pool empty — new roles need a restart until refilled"
    (ws / ".tower" / "dsh_pool.json").write_text(
        json.dumps({"size": 2, "free": [], "claimed": {"fe": "P-1", "se": "P-2"}}), encoding="utf-8"
    )
    assert bootstrap.pool_warning(cfg) == bootstrap.POOL_EMPTY_WARNING


def test_pool_state_reports_an_unreadable_record(tower):
    toml, ws, _fake, _registry = tower
    cfg = load_config(toml.parent)
    (ws / ".tower" / "dsh_pool.json").write_text("nope", encoding="utf-8")
    state = bootstrap.pool_state(cfg)
    assert state["size"] == 2 and state["free"] == []
    assert "unreadable" in state["error"]
    assert bootstrap.load_pool(cfg) is None


@pytest.mark.parametrize("raw, expected", [(None, 0), ("3", 3), ("nonsense", 0), (-4, 0)])
def test_pool_size_is_read_defensively(raw, expected):
    cfg = SimpleNamespace(adapter_options={"dsh": {} if raw is None else {"pool_size": raw}})
    assert bootstrap.pool_size(cfg) == expected
