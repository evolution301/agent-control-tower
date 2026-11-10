"""Tests must never touch the real `~/.tower`.

`tower dispatch` binds the tower that did the work, and `tower projects …` reads
the registry; a test that forgets `TOWER_HOME` rewrites the developer's real
binding to a pytest temp dir (it did: the panel then reported a `triage-tower`).
This autouse fixture gives every test its own throwaway home.
"""

import pytest


@pytest.fixture(autouse=True)
def isolated_tower_home(tmp_path, monkeypatch):
    home = tmp_path / "tower-home"
    monkeypatch.setenv("TOWER_HOME", str(home))
    return home


@pytest.fixture(autouse=True)
def never_spawn_worker(monkeypatch):
    """B2: `dispatch --async` must never leave a detached worker behind.

    Tests enqueue jobs and drain them with `worker.run_worker(once=True)`
    in-process; a real detached spawn here piled up 44 immortal workers that
    polled deleted tmp dirs at full CPU (2026-11-09 field report).
    """
    from control_tower import worker as worker_mod

    spawns = []

    def fake_ensure(cfg):
        spawns.append(str(cfg.workspace))
        # emulate a real spawn: the worker's first heartbeat exists by the time
        # dispatch/review check for a consumer
        worker_mod._heartbeat(cfg)
        return True

    real = worker_mod.ensure_worker
    monkeypatch.setattr(worker_mod, "_real_ensure_worker", real, raising=False)
    monkeypatch.setattr(worker_mod, "ensure_worker", fake_ensure)
    yield spawns
