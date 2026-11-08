"""The control room's data and buttons must be real, not decorative."""

import json
import threading
import urllib.request

import pytest

from control_tower import scene_actions
from control_tower.board import STATUS_ACCEPTED, STATUS_REPORTED, STATUS_REWORK, Board
from control_tower.config import ensure_layout, parse_config
from control_tower.scene import MAX_STUCK, RATE_USD, build_scene


@pytest.fixture()
def tower(tmp_path, monkeypatch):
    config = {
        "name": "scene-tower",
        "workspace": str(tmp_path),
        "roles": {
            "frontend": {"description": "code", "adapter": "mock", "write_scopes": ["roles/frontend/"]},
            "ops": {"description": "ops", "adapter": "mock", "write_scopes": ["roles/ops/"]},
            "research": {"description": "research", "adapter": "mock", "max_rounds": 0},
        },
        "verify_commands": [],
    }
    path = tmp_path / "tower.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    cfg = parse_config(path)
    ensure_layout(cfg)
    monkeypatch.chdir(tmp_path)
    return cfg


def _report(task, tmp_path, name="out.txt"):
    rel = f"roles/{task.role}/{name}"
    (tmp_path / "roles" / task.role).mkdir(parents=True, exist_ok=True)
    (tmp_path / rel).write_text("x", encoding="utf-8")
    return {"state": "ready", "summary": "done", "files": [rel], "checks": ["pytest → 3 passed"]}


def test_floors_are_the_roles_and_agents_map_one_to_one(tower):
    scene = build_scene(tower)
    assert [f["id"] for f in scene["floors"]] == ["frontend", "ops", "research"]
    assert [a["id"] for a in scene["agents"]] == ["frontend", "ops", "research"]
    assert [f["level"] for f in scene["floors"]] == [1, 2, 3]


def test_a_blocked_task_shows_up_as_a_question(tower):
    board = Board(tower)
    task = board.create("frontend", "needs a decision")
    task.state, task.attention = "blocked", {"question": "staging or prod?"}
    board.save(task)

    scene = build_scene(tower, board)
    pending = scene["pending"]
    assert len(pending) == 1
    assert pending[0]["kind"] == "question"
    assert pending[0]["reason"] == "staging or prod?"
    assert scene["kpis"]["waiting"] == 1


def test_a_failed_task_shows_up_as_stuck_with_a_real_reason(tower):
    board = Board(tower)
    task = board.create("ops", "will fail")
    board.transition(task, "failed", note="adapter exit 4")
    scene = build_scene(tower, board)
    stuck = [p for p in scene["pending"] if p["kind"] == "stuck"]
    assert stuck and "adapter exit 4" in stuck[0]["reason"]
    assert scene["kpis"]["stuck"] == 1


def test_stuck_cards_are_capped_but_counted_in_full(tower):
    board = Board(tower)
    for index in range(5):
        task = board.create("ops", f"fail {index}")
        board.transition(task, "failed", note=f"adapter exit {index}")
    scene = build_scene(tower, board)
    assert len([p for p in scene["pending"] if p["kind"] == "stuck"]) <= MAX_STUCK
    assert scene["stuck_total"] == 5


def test_cost_is_runs_times_the_stated_rate(tower):
    board = Board(tower)
    task = board.create("frontend", "one run")
    task.assignments = [{"adapter": "mock", "run": 1}]
    board.save(task)
    scene = build_scene(tower, board)
    assert scene["cost"]["est_usd"] == round(RATE_USD, 2)
    assert scene["cost"]["runs"] == 1
    assert scene["rate_usd"] == RATE_USD


def test_completed_today_comes_from_the_task_history(tower, tmp_path):
    board = Board(tower)
    task = board.create("frontend", "done today")
    board.transition(task, STATUS_ACCEPTED, note="approved")
    scene = build_scene(tower, board)
    assert any(c["task"] == task.id for c in scene["completed_today"])


def test_scheduled_shows_a_countdown(tower):
    board = Board(tower)
    task = board.create("ops", "due soon")
    task.due_at = "2026-12-01T09:00:00"
    board.save(task)
    scene = build_scene(tower, board)
    assert scene["scheduled"] and scene["scheduled"][0]["task"] == task.id


def test_log_is_newest_first(tower):
    board = Board(tower)
    board.create("frontend", "first")
    board.create("ops", "second")
    scene = build_scene(tower, board)
    assert scene["log"]
    assert scene["log"][0]["event"]


# ---- the buttons ----------------------------------------------------------

def test_approve_refuses_a_task_that_would_not_verify(tower):
    board = Board(tower)
    task = board.create("frontend", "no report")
    with pytest.raises(ValueError):
        scene_actions.perform(tower, board, "approve", task.id)


def test_approve_accepts_a_verifiable_task_and_logs_it(tower, tmp_path):
    board = Board(tower)
    task = board.create("frontend", "good")
    task.report = _report(task, tmp_path)
    board.save(task)
    board.transition(task, STATUS_REPORTED, note="filed", report=task.report)

    result = scene_actions.perform(tower, board, "approve", task.id)
    assert result["outcome"] == "accepted"
    assert board.load(task.id).status == STATUS_ACCEPTED
    assert "scene_approved" in tower.ledger_path.read_text(encoding="utf-8")


def test_reply_requires_text_and_dispatches(tower, monkeypatch):
    board = Board(tower)
    task = board.create("frontend", "waiting")
    task.state, task.attention = "blocked", {"question": "which env?"}
    board.save(task)

    with pytest.raises(ValueError):
        scene_actions.perform(tower, board, "reply", task.id, "")

    sent = []
    monkeypatch.setattr(scene_actions, "_dispatch", lambda cfg, tid, note="": sent.append((tid, note)))
    result = scene_actions.perform(tower, board, "reply", task.id, "use staging")
    assert result["outcome"] == "redispatched"
    assert sent == [(task.id, "use staging")]
    assert board.load(task.id).status == STATUS_REWORK


def test_skip_clears_the_question(tower):
    board = Board(tower)
    task = board.create("frontend", "asked")
    task.state, task.attention = "blocked", {"question": "?"}
    board.save(task)
    assert scene_actions.perform(tower, board, "skip", task.id)["outcome"] == "dismissed"
    assert board.load(task.id).attention is None


def test_unknown_action_is_rejected(tower):
    board = Board(tower)
    task = board.create("frontend", "x")
    with pytest.raises(KeyError):
        scene_actions.perform(tower, board, "launch-missiles", task.id)


# ---- routes ---------------------------------------------------------------

def test_scene_page_and_api_are_served(tower):
    from http.server import ThreadingHTTPServer

    from control_tower.panel import make_handler

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tower))
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/scene", timeout=5) as r:
            page = r.read().decode("utf-8")
        assert "<canvas" in page and "/api/scene" in page
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/scene", timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
        assert set(data) >= {"floors", "agents", "pending", "cost", "kpis"}
        # the UI must never call itself demo/mock (adapter names may be anything)
        assert "demo" not in page.lower() and "mock" not in page.lower()
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_action_endpoint_rejects_garbage(tower):
    from http.server import ThreadingHTTPServer

    from control_tower.panel import make_handler

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tower))
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        request = urllib.request.Request(f"http://127.0.0.1:{port}/api/actions",
                                       data=json.dumps({"action": "nope", "task": "T-0001"}).encode(),
                                       headers={"Content-Type": "application/json"})
        with pytest.raises(urllib.error.HTTPError) as info:
            urllib.request.urlopen(request, timeout=5)
        assert info.value.code == 400
    finally:
        httpd.shutdown()
        httpd.server_close()
