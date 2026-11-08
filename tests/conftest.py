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
