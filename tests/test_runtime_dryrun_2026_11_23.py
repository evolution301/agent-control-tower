"""P0-2 / P1-3: a dry run must be a preview, not a dispatch.

`tower dispatch qa "x" --dry-run` wrote `.tower/tasks/T-0218.json` and burned the
id; these pins keep the preview in memory: no file, no task dir, no consumed
T-id, no group attach.
"""

from __future__ import annotations

import pytest

from control_tower import dispatch as dispatch_mod
from control_tower import groups
from control_tower.adapters.base import AdapterResult
from control_tower.board import Board
from control_tower.config import next_task_id, parse_config
from control_tower.dispatch import DRY_RUN_STATUS, build_task, dispatch


class _OkAdapter:
    name = "mock"

    def run(self, req):  # noqa: ANN001 - duck typed on purpose
        return AdapterResult(exit_code=0, text="## Status\nreported\n\n## Summary\nfine\n")


def _tower(tmp_path):
    workspace = tmp_path / "tower"
    workspace.mkdir(exist_ok=True)
    path = tmp_path / "tower.toml"
    path.write_text(
        "\n".join([
            "name: runtime",
            f"workspace: {workspace.as_posix()}",
            "roles:",
            "  qa:",
            '    description: "qa"',
            "    adapter: mock",
            "adapters:",
            "  dsh:",
            '    base_url: "http://127.0.0.1:1"',
        ]) + "\n",
        encoding="utf-8",
    )
    return parse_config(path)


def test_dry_run_writes_nothing_and_burns_no_id(tmp_path, monkeypatch):
    cfg = _tower(tmp_path)
    board = Board(cfg)
    before = next_task_id(cfg)

    preview = build_task(cfg, "qa", "x")
    assert not (cfg.tasks_dir / f"{preview.id}.json").exists(), "build_task must not save"

    monkeypatch.setattr(dispatch_mod, "get_adapter", lambda name, options: _OkAdapter())
    out = dispatch(cfg, board, preview, dry_run=True)

    assert out.id == before
    assert out.status == DRY_RUN_STATUS
    assert not (cfg.tasks_dir / f"{preview.id}.json").exists()
    assert not cfg.task_dir(preview.id).exists()
    assert next_task_id(cfg) == before, "a preview must not consume a T-id"
    assert dispatch(cfg, board, build_task(cfg, "qa", "y"), dry_run=True).id == before


def test_dry_run_preview_names_the_group_and_attaches_nothing(tmp_path, monkeypatch):
    cfg = _tower(tmp_path)
    group = groups.create(cfg, "g", ["qa"])
    monkeypatch.setattr(dispatch_mod, "get_adapter", lambda name, options: _OkAdapter())

    out = dispatch(cfg, Board(cfg), build_task(cfg, "qa", "x"), dry_run=True, group=group.id)

    assert out.group == group.id
    assert groups.load(cfg, group.id).tasks == [], "a preview must not attach a phantom id"


def test_the_real_dispatch_does_attach_the_task(tmp_path, monkeypatch):
    cfg = _tower(tmp_path)
    group = groups.create(cfg, "g", ["qa"])
    board = Board(cfg)
    task = board.create("qa", "x")

    monkeypatch.setattr(dispatch_mod, "get_adapter", lambda name, options: _OkAdapter())
    done = dispatch(cfg, board, task, group=group.id)

    assert done.id in groups.load(cfg, group.id).tasks
