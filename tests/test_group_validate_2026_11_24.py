"""A `--group` that names no room must be refused, not silently carried.

aimigao-zh (2026-11-24): `tower dispatch cli --group G-0002` in a workspace whose
groups stopped at G-0001 was accepted. The task was saved, the job enqueued, and
the worker settled it in under a second with `{"outcome": "missing-task"}` while
the board still showed a `pending` task — silence for the operator.
"""

import json

import pytest

from control_tower import cli
from control_tower import groups
from control_tower import worker
from control_tower.adapters.base import AdapterResult
from control_tower.board import Board, STATUS_FAILED, STATUS_REPORTED, STATUS_IN_PROGRESS
from control_tower.config import ensure_layout, parse_config
from control_tower.dispatch import (
    build_task,
    dispatch,
    group_refusal,
    require_group,
)

REPORT = """## Status
ready

## Summary
done

## Files
- none

## Checks
- none
"""


class _OkAdapter:
    name = "ok"

    def run(self, req):
        return AdapterResult(text=REPORT, exit_code=0)


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    config = {
        "name": "group-tower",
        "workspace": str(tmp_path / "tower"),
        "roles": {"qa": {"description": "qa", "adapter": "mock"}},
        "adapters": {"dsh": {"base_url": "http://127.0.0.1:1"}},
    }
    path = tmp_path / "tower.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    out = parse_config(path)
    ensure_layout(out)
    monkeypatch.setattr("control_tower.dispatch.get_adapter", lambda name, opts: _OkAdapter())
    return out


def _ledger(cfg):
    if not cfg.ledger_path.exists():
        return []
    return [json.loads(line) for line in cfg.ledger_path.read_text(encoding="utf-8").splitlines()]


def test_an_unknown_group_refuses_with_the_tower_group_list_line(cfg):
    message = f"no such group G-0002 in {cfg.workspace} — run `tower group list`"

    assert group_refusal(cfg, "G-0002") == message
    with pytest.raises(ValueError) as raised:
        require_group(cfg, "G-0002")
    assert str(raised.value) == message

    real = groups.create(cfg, "room", ["qa"])
    assert require_group(cfg, real.id).id == real.id
    assert group_refusal(cfg, real.id) == ""
    assert group_refusal(cfg, "") == "", "no --group is not a refusal"


def test_dispatch_refuses_a_phantom_group_before_it_writes_anything(cfg):
    board = Board(cfg)
    task = board.create("qa", "verify the pages")
    task.group = "G-0002"
    board.save(task)

    with pytest.raises(ValueError, match="no such group G-0002"):
        dispatch(cfg, board, task)

    assert not (cfg.task_dir(task.id) / "prompt.md").exists(), "no prompt was written"
    assert [line["event"] for line in _ledger(cfg)].count("dispatch_contract") == 0
    assert groups.all_groups(cfg) == [], "no room was invented for it"


def test_a_dry_run_refuses_a_phantom_group_too(cfg):
    board = Board(cfg)
    task = build_task(cfg, "qa", "verify the pages")

    with pytest.raises(ValueError, match="no such group G-0002"):
        dispatch(cfg, board, task, dry_run=True, group="G-0002")

    assert not (cfg.tasks_dir / f"{task.id}.json").exists()
    assert [line["event"] for line in _ledger(cfg)].count("dispatch_dry_run") == 0


def test_the_cli_refuses_an_unknown_group_with_a_message_and_a_nonzero_exit(tmp_path, capsys):
    toml = tmp_path / "tower.toml"
    toml.write_text("\n".join([
        "name: group-cli",
        f"workspace: {(tmp_path / 'tower').as_posix()}",
        "roles:",
        "  qa:",
        '    description: "qa"',
        "    adapter: mock",
        "adapters:",
        "  dsh:",
        '    base_url: "http://127.0.0.1:1"',
        "",
    ]), encoding="utf-8")

    code = cli.main(["--workspace", str(toml), "dispatch", "qa", "x", "--group", "G-0002"])

    err = capsys.readouterr().err
    assert code != 0, "a refused dispatch must not report success"
    assert "no such group G-0002" in err
    assert "tower group list" in err
    assert worker.queue_depth(parse_config(toml)) == 0, "nothing was queued behind it"


def test_a_real_group_still_dispatches_and_holds_the_task(cfg):
    board = Board(cfg)
    room = groups.create(cfg, "room", ["qa"])
    task = board.create("qa", "verify the pages")
    task.group = room.id
    board.save(task)

    out = dispatch(cfg, board, task)

    assert out.status == STATUS_REPORTED
    assert out.group == room.id
    assert groups.load(cfg, room.id).tasks == [task.id]


def test_a_worker_job_for_a_missing_group_fails_the_task_loudly(cfg):
    board = Board(cfg)
    task = board.create("qa", "verify the pages")
    task.group = "G-0002"
    task.mode = "async"
    board.save(task)
    worker.enqueue(cfg, "dispatch", task.id)

    assert worker.run_worker(cfg, once=True) == 0

    failed = board.load(task.id)
    assert failed.status == STATUS_FAILED, "a settled job must not leave the task pending"
    assert "no such group G-0002" in (failed.history[-1].get("note") or "")
    events = _ledger(cfg)
    assert any(line["event"] == "async_failed" and line.get("failed_task") == task.id
               for line in events)
    assert failed.status != STATUS_IN_PROGRESS
