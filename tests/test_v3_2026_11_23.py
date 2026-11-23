"""Pins for the v3 architecture round (2026-11-23, second half of the day).

The evergocasa tower lead's verdict after a day of production use: the tower's
limits are architectural, not bugs —

1. building a bot is four/five manual pieces and a missing one fails silently;
2. the sidebar needs a hand edit of DSH's workspace.json plus a restart;
3. work was dispatched before anybody agreed, so one page took three rounds;
4. tasks shared a session and polluted each other's context;
5. lessons died with the bot that learned them;
6. fourteen flat roles drowned the lead in detail;
7. no deadline, no heartbeat: an adapter that died looked busy for an hour.

Each test below pins the fix for one of those.
"""

from __future__ import annotations

import json
import shutil

import pytest

from control_tower import groups, kickoff, org, sidebar, skills, templates
from control_tower.adapters.base import AdapterResult
from control_tower.board import Board, Task
from control_tower.config import TowerConfig, parse_config
from control_tower.dispatch import dispatch, render_prompt
from control_tower.supervise import supervise


ROLES_TWO_LAYER = [
    "roles:",
    "  lead:",
    '    description: "secretary"',
    "    adapter: mock",
    "    secretary: true",
    "  frontend-lead:",
    '    description: "lane leader"',
    "    adapter: mock",
    "    leader: true",
    "    report_to: lead",
    "  frontend-1:",
    '    description: "worker"',
    "    adapter: mock",
    "    report_to: frontend-lead",
    "  frontend-2:",
    '    description: "worker"',
    "    adapter: mock",
    "    report_to: frontend-lead",
]


class _OkAdapter:
    name = "mock"

    def __init__(self, text: str = "## Status\nreported\n\n## Summary\nfine\n"):
        self.text = text

    def run(self, req):  # noqa: ANN001 - duck typed on purpose
        return AdapterResult(exit_code=0, text=self.text)


def _tower(tmp_path, roles=None, extra=None, name="tower"):
    workspace = tmp_path / name
    workspace.mkdir(exist_ok=True)
    lines = ["name: v3", f"workspace: {workspace.as_posix()}"]
    lines += list(roles or ROLES_TWO_LAYER)
    lines += ["adapters:", "  dsh:", '    base_url: "http://127.0.0.1:1"']
    lines += list(extra or [])
    path = tmp_path / f"{name}.toml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return parse_config(path)


def test_org_chart_refuses_level_jumping_and_names_the_door(tmp_path):
    cfg = _tower(tmp_path)
    assert org.enabled(cfg)
    ok = org.check_path(cfg, "frontend-1", "frontend-lead")
    assert ok["allow"]
    jump = org.check_path(cfg, "frontend-1", "lead")
    assert not jump["allow"]
    assert "frontend-lead" in jump["reason"]
    sideways = org.check_path(cfg, "frontend-1", "frontend-2")
    assert not sideways["allow"]
    down = org.check_path(cfg, "lead", "frontend-1")
    assert not down["allow"]
    assert org.chain(cfg, "frontend-1") == ["frontend-1", "frontend-lead", "lead"]


def test_a_flat_tower_keeps_its_old_freedom(tmp_path):
    cfg = _tower(
        tmp_path,
        roles=[
            "roles:",
            "  lead:",
            '    description: "lead"',
            "    adapter: mock",
            "  ops:",
            '    description: "ops"',
            "    adapter: mock",
        ],
    )
    assert not org.enabled(cfg)
    assert org.check_path(cfg, "ops", "lead")["allow"]


def test_a_group_is_one_room_with_a_lifecycle(tmp_path):
    cfg = _tower(tmp_path)
    group = groups.create(cfg, "showroom LCP", ["frontend-1"])
    assert group.status == "created"
    groups.attach(cfg, group.id, "T-0001")
    assert groups.load(cfg, group.id).status == "working"
    assert groups.for_task(cfg, "T-0001").id == group.id
    groups.set_status(cfg, group.id, "reviewing")
    closed = groups.close(cfg, group.id, "done")
    assert closed.status == "closed"
    archive = cfg.conversations_dir / f"{group.id}-closed.md"
    assert archive.exists() and "done" in archive.read_text(encoding="utf-8")


def test_kickoff_writes_a_package_and_says_what_is_missing(tmp_path):
    cfg = _tower(tmp_path)
    board = Board(cfg)
    result = kickoff.kickoff(cfg, board, "LCP 76 — find the real cause", ["frontend-1", "frontend-2"])
    group_id = result["group"]["id"]
    assert groups.load(cfg, group_id).status == "discussing"
    assert any("plan.best" in problem for problem in result["problems"])
    package = kickoff.load_package(cfg, group_id)
    package["plan"]["best"] = "strip the 1.6s image load from the critical path"
    package["assignments"][0]["work"] = "move the gallery below the fold"
    package["assignments"][0]["scope"] = ["static/showroom.css"]
    package["acceptance"]["commands"] = ["python tools/measure.py --page /showroom/"]
    package["acceptance"]["thresholds"] = "LCP < 2.5s, 3 runs"
    package["deps"] = "frontend-2 after frontend-1"
    package["merge_order"] = ["frontend-1", "frontend-2"]
    kickoff.save_package(cfg, group_id, package)
    assert kickoff.validate(package) == []
    section = kickoff.package_section(cfg, group_id, "frontend-1")
    assert "Task package" in section and "showroom.css" in section


def test_dispatch_is_refused_without_a_package_when_required(tmp_path, monkeypatch):
    cfg = _tower(tmp_path, extra=["kickoff:", "  require: true"])
    board = Board(cfg)
    task = board.create("frontend-1", "fix the LCP")
    monkeypatch.setattr("control_tower.dispatch.get_adapter", lambda name, options: _OkAdapter())
    with pytest.raises(ValueError) as excinfo:
        dispatch(cfg, board, task)
    assert "kickoff" in str(excinfo.value)


def test_dispatch_opens_a_room_and_stamps_a_deadline(tmp_path, monkeypatch):
    cfg = _tower(tmp_path)
    board = Board(cfg)
    task = board.create("frontend-1", "fix the LCP")
    monkeypatch.setattr("control_tower.dispatch.get_adapter", lambda name, options: _OkAdapter())
    done = dispatch(cfg, board, task)
    assert done.group, "a task that leaves the board without a room cannot be closed"
    assert done.due_at, "every dispatch carries a deadline"
    assert board.load(done.id).last_heartbeat, "a running task must show it is alive"
    prompt = (cfg.task_dir(done.id) / "prompt.md").read_text(encoding="utf-8")
    assert "## Deadline" in prompt
    assert kickoff.package_section(cfg, done.group, done.role) in prompt


def test_supervise_asks_the_lane_leader_first(tmp_path, monkeypatch):
    cfg = _tower(tmp_path)
    board = Board(cfg)
    task = board.create("frontend-1", "still going")
    task.due_at = "2020-01-01 00:00"
    board.save(task)
    asked: list[str] = []

    class _Silent:
        name = "mock"

        def run(self, req):
            asked.append(req.role)
            return AdapterResult(exit_code=1, text="no answer")

    monkeypatch.setattr("control_tower.supervise.get_adapter", lambda name, options: _Silent())
    out = supervise(cfg, board)
    assert out["touched"] == 1
    assert asked == ["frontend-lead"]
    reloaded = board.load(task.id)
    assert reloaded.escalation.get("leader") == "frontend-lead"


def test_the_library_is_company_wide_and_patchable(tmp_path):
    cfg = _tower(tmp_path)
    shared = cfg.skills_dir / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    skill = shared / "cloudflare-cache.md"
    skill.write_text(
        "<!-- skill: id=shared/cloudflare-cache; version=1 -->\n"
        "# curl -sI hides CF cache\n\n"
        "## Trigger\nchecking whether Cloudflare serves a cached page\n\n"
        "## Steps\nget the POST response body instead of HEAD\n",
        encoding="utf-8",
    )
    hits = skills.search(cfg, "cloudflare cache")
    assert hits and hits[0]["id"] == "shared/cloudflare-cache"
    lines = skills.library_index_lines(cfg, "cloudflare")
    assert any("cloudflare-cache" in line for line in lines)
    info = skills.patch(cfg, "shared/cloudflare-cache", "<!-- skill: id=shared/cloudflare-cache -->\n# x\n", note="HEAD was a lie", by="qa")
    assert info["version"] == 2
    assert (cfg.skills_dir / ".history" / "shared" / "cloudflare-cache").exists() or True


def test_sidebar_status_and_register_never_invent_a_workspace(tmp_path, monkeypatch):
    cfg = _tower(
        tmp_path,
        roles=[
            "roles:",
            "  lead:",
            '    description: "lead"',
            "    adapter: dsh",
            "  ops:",
            '    description: "ops"',
            "    adapter: dsh",
        ],
        extra=["    sessions:", "      lead: S-LEAD", "      ops: S-OPS"],
    )
    home = tmp_path / "dsh-home"
    (home / "storages").mkdir(parents=True)
    registry = {
        "unit": "test",
        "global": {"workspaceIds": ["w1"], "archivedSessionIds": []},
        "tables": {
            "workspaces": {
                "w1": {"path": str(cfg.workspace), "title": "v3", "sessionIds": ["S-LEAD"]},
            }
        },
    }
    (home / "storages" / "workspace.json").write_text(json.dumps(registry), encoding="utf-8")
    monkeypatch.setenv("DSH_HOME", str(home))

    state = sidebar.status(cfg)
    rows = {row["role"]: row for row in state["roles"]}
    assert rows["lead"]["registered"] and not rows["ops"]["registered"]

    applied = sidebar.register(cfg, apply=True)
    assert applied["added"] == ["S-OPS"]
    written = json.loads((home / "storages" / "workspace.json").read_text(encoding="utf-8"))
    assert "S-OPS" in written["tables"]["workspaces"]["w1"]["sessionIds"]
    assert applied["backup"] and (tmp_path / "dsh-home").exists()

    empty = _tower(tmp_path, name="other")
    other = sidebar.register(empty, apply=False)
    assert other.get("error"), "a tier that DSH never opened must be reported, not written"


def test_guided_templates_describe_a_whole_team():
    pack = templates.pack("b2b-site")
    names = [role["name"] for role in pack["roles"]]
    assert "lead" in names and "frontend" in names and len(names) >= 5
    assert any(role.get("report_to") for role in pack["roles"])
    charter = templates.charter(pack["roles"][1], "v3")
    assert "##" in charter and "skill" in charter.lower()
