"""Multi-project board: registry, active pointer, switcher endpoints (option C).

Each project keeps its own `.tower/`; the registry only says where the projects
are and which one is active. These tests use `TOWER_HOME` so they never touch the
real `~/.tower`.
"""

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from control_tower import registry
from control_tower.board import Board
from control_tower.config import ensure_layout, parse_config
from control_tower.panel import make_handler


def _make_project(root, name, roles=("backend",)):
    root.mkdir(parents=True, exist_ok=True)
    config = {
        "name": name,
        "workspace": str(root),
        "roles": {role: {"description": role, "adapter": "mock"} for role in roles},
        "verify_commands": [],
    }
    path = root / "tower.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    cfg = parse_config(path)
    ensure_layout(cfg)
    return cfg


@pytest.fixture()
def two_projects(tmp_path, monkeypatch):
    home = tmp_path / "tower-home"
    monkeypatch.setenv("TOWER_HOME", str(home))
    cfg_a = _make_project(tmp_path / "alpha", "alpha", roles=("backend", "qa"))
    cfg_b = _make_project(tmp_path / "beta", "beta", roles=("writer",))
    return cfg_a, cfg_b


def test_add_discover_and_list_projects(two_projects, tmp_path):
    cfg_a, cfg_b = two_projects
    a = registry.add_project(cfg_a.workspace)
    b = registry.add_project(cfg_b.workspace)
    assert [p["name"] for p in registry.load_projects()] == ["alpha", "beta"]
    assert a["color"] != b["color"], "each project gets its own identity colour"

    found = registry.discover([tmp_path])
    assert {str(p) for p in found} == {str(cfg_a.workspace.resolve()), str(cfg_b.workspace.resolve())}


def test_use_writes_the_active_pointer_and_resolve_follows_it(two_projects):
    cfg_a, cfg_b = two_projects
    registry.add_project(cfg_a.workspace)
    registry.add_project(cfg_b.workspace)
    registry.set_active("beta")
    assert registry.get_active()["name"] == "beta"
    assert registry.resolve() == cfg_b.workspace.resolve()
    assert registry.resolve("alpha") == cfg_a.workspace.resolve()


def test_unknown_project_is_an_error(two_projects):
    with pytest.raises(KeyError):
        registry.resolve("ghost")
    with pytest.raises(ValueError):
        registry.add_project(two_projects[0].workspace / "nowhere")


def test_remove_forgets_but_keeps_files(two_projects):
    cfg_a, cfg_b = two_projects
    registry.add_project(cfg_a.workspace)
    registry.add_project(cfg_b.workspace)
    registry.set_active("beta")
    registry.remove_project("beta")
    assert [p["name"] for p in registry.load_projects()] == ["alpha"]
    assert registry.get_active() is None
    assert (cfg_b.workspace / "tower.json").exists()


def test_overview_sums_and_survives_a_broken_project(two_projects, tmp_path):
    cfg_a, cfg_b = two_projects
    registry.add_project(cfg_a.workspace)
    registry.add_project(cfg_b.workspace)
    board = Board(cfg_a)
    board.create("backend", "one task")
    board.create("qa", "another task")

    # a registered project whose config is gone must be reported, not crash the board
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "tower.json").write_text("{ not json", encoding="utf-8")
    registry.add_project(broken)

    data = registry.overview()
    assert len(data["projects"]) == 3
    alpha = [p for p in data["projects"] if p["name"] == "alpha"][0]
    assert alpha["tasks"] == 2
    assert alpha["bots"] == 2
    broken_row = [p for p in data["projects"] if p["name"] == "broken"][0]
    assert broken_row["ok"] is False and broken_row["error"]
    assert data["totals"]["tasks"] == 2


def test_cli_projects_list_add_use_remove(two_projects, capsys):
    from control_tower import cli

    cfg_a, cfg_b = two_projects
    assert cli.main(["projects", "add", str(cfg_a.workspace)]) == 0
    assert cli.main(["projects", "add", str(cfg_b.workspace)]) == 0
    assert cli.main(["projects", "use", "alpha"]) == 0
    out = capsys.readouterr().out
    assert "registered" in out or "active project" in out

    assert cli.main(["projects", "list"]) == 0
    listing = capsys.readouterr().out
    assert "alpha" in listing and "beta" in listing
    assert "●" in listing, "the active project is marked"

    assert cli.main(["projects", "remove", "beta"]) == 0
    assert [p["name"] for p in registry.load_projects()] == ["alpha"]


def test_stats_all_aggregates_projects(two_projects, capsys):
    from control_tower import cli

    cfg_a, cfg_b = two_projects
    registry.add_project(cfg_a.workspace)
    registry.add_project(cfg_b.workspace)
    Board(cfg_a).create("backend", "task in alpha")
    Board(cfg_b).create("writer", "task in beta")
    assert cli.main(["stats", "--all"]) == 0
    out = capsys.readouterr().out
    assert "all projects" in out
    assert "alpha" in out and "beta" in out
    assert "projects 2" in out


def test_panel_switcher_endpoints(two_projects, monkeypatch):
    cfg_a, cfg_b = two_projects
    registry.add_project(cfg_a.workspace)
    registry.add_project(cfg_b.workspace)
    registry.set_active("alpha")
    Board(cfg_a).create("backend", "alpha work")

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cfg_a))
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/projects", timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
        assert [p["name"] for p in data["projects"]] == ["alpha", "beta"]
        assert data["active"] == "alpha"

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/snapshot?project=beta", timeout=5) as r:
            snap = json.loads(r.read().decode("utf-8"))
        assert snap["name"] == "beta"

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/stats?project=alpha", timeout=5) as r:
            stats = json.loads(r.read().decode("utf-8"))
        assert stats["totals"]["tasks"] == 1

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/snapshot", timeout=5) as r:
            started = json.loads(r.read().decode("utf-8"))
        assert started["name"] == "alpha", "no ?project means the workspace the panel was started in"

        # a stale bookmark (renamed project) must not blank the board
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/snapshot?project=ghost", timeout=5) as r:
            fallback = json.loads(r.read().decode("utf-8"))
        assert fallback["name"] == "alpha"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_page_renders_the_switcher(two_projects):
    from control_tower.panel import render_page

    cfg_a, _ = two_projects
    page = render_page(cfg_a, "zh")
    assert "/api/projects" in page and "/api/snapshot" in page
    assert 'id="teams"' in page, "the project switcher container exists"
    assert 'data-p="__all__"' in page, "the aggregate chip is the first entry"
    assert "全部" in page


# ------------------------------------------------------- "don't lose my tower"


def test_configured_workspace_always_wins_over_pointer_and_binding(two_projects):
    """A plugin update must never re-aim a working installation."""
    cfg_a, cfg_b = two_projects
    registry.add_project(cfg_a.workspace)
    registry.add_project(cfg_b.workspace)
    registry.set_active("alpha")          # the switcher points at alpha
    registry.record_binding(cfg_b.workspace)  # last used was beta

    # the profile config says alpha: it wins, whatever the other two say
    assert registry.effective_workspace(cfg_a.workspace) == cfg_a.workspace.resolve()


def test_missing_config_reconnects_to_the_bound_tower(two_projects):
    """The seamless case: the config line is gone after an update."""
    cfg_a, cfg_b = two_projects
    registry.add_project(cfg_a.workspace)
    registry.add_project(cfg_b.workspace)
    registry.set_active("alpha")
    registry.record_binding(cfg_b.workspace)

    assert registry.effective_workspace(None) == cfg_b.workspace.resolve()
    # a stale path (deleted project) must not be followed either
    assert registry.effective_workspace("/definitely/not/a/tower") == cfg_b.workspace.resolve()


def test_binding_only_falls_back_to_the_active_project(two_projects):
    cfg_a, cfg_b = two_projects
    registry.add_project(cfg_a.workspace)
    registry.add_project(cfg_b.workspace)
    registry.set_active("alpha")
    registry.clear_binding()   # `use` binds too; clear it to isolate the last resort
    assert registry.read_binding() is None
    assert registry.effective_workspace(None) == cfg_a.workspace.resolve()


def test_record_binding_refuses_a_directory_without_a_tower(two_projects, tmp_path):
    assert registry.record_binding(tmp_path / "empty") is None
    assert registry.read_binding() is None


def test_binding_does_not_rewrite_when_unchanged(two_projects):
    cfg_a, _ = two_projects
    first = registry.record_binding(cfg_a.workspace, source="cli")
    stamp = first["recorded_at"]
    again = registry.record_binding(cfg_a.workspace, source="cli")
    assert again["recorded_at"] == stamp, "no churn: the same tower keeps its record"


def test_doctor_warns_when_the_aim_and_the_binding_disagree(two_projects, capsys):
    from control_tower import cli

    cfg_a, cfg_b = two_projects
    registry.record_binding(cfg_b.workspace)
    (cfg_a.workspace / "tower.json").write_text(json.dumps({
        "name": "alpha", "workspace": str(cfg_a.workspace),
        "roles": {"backend": {"description": "b", "adapter": "mock"}},
        "verify_commands": [],
    }), encoding="utf-8")
    assert cli.main(["--workspace", str(cfg_a.workspace), "doctor"]) == 0
    out = capsys.readouterr().out
    assert "aim:" in out and "beta" in out, "a mismatched aim must be visible before a dispatch"
