"""A board a person bookmarked must keep working: stable port selection."""

import json
import socket

import pytest

from control_tower import panel
from control_tower.config import ensure_layout, parse_config


@pytest.fixture()
def tower(tmp_path, monkeypatch):
    config = {
        "name": "port-test",
        "workspace": str(tmp_path),
        "roles": {"backend": {"description": "s", "adapter": "mock"}},
    }
    path = tmp_path / "tower.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    cfg = parse_config(path)
    ensure_layout(cfg)
    monkeypatch.setattr(panel, "DEFAULT_PORT", 8890)
    return cfg


def _busy(port: int):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", port))
    sock.listen(1)
    return sock


def test_an_explicit_port_is_used_verbatim_and_remembered(tower):
    assert panel.choose_port(tower, 8899) == 8899
    assert panel.remembered_port(tower) == 8899


def test_the_remembered_port_wins_on_the_next_run(tower):
    panel.choose_port(tower, 8901)
    # even if the default is free again, the workspace keeps its own URL
    assert panel.choose_port(tower) == 8901


def test_a_busy_port_shifts_to_the_next_free_one_and_sticks(tower):
    blocker = _busy(8890)
    try:
        chosen = panel.choose_port(tower)
        assert chosen != 8890
        assert panel.remembered_port(tower) == chosen
        # second run: same port, no drift
        assert panel.choose_port(tower) == chosen
    finally:
        blocker.close()


def test_a_remembered_port_that_became_busy_shifts_again(tower):
    panel.choose_port(tower, 8902)
    blocker = _busy(8902)
    try:
        chosen = panel.choose_port(tower)
        assert chosen != 8902
        assert panel.remembered_port(tower) == chosen
    finally:
        blocker.close()


def test_port_free_reports_reality(tower):
    assert panel.port_free(8893)
    blocker = _busy(8893)
    try:
        assert not panel.port_free(8893)
    finally:
        blocker.close()


def test_remembered_port_ignores_junk(tower):
    (tower.state_dir / panel.PORT_MEMO).write_text("not-a-port", encoding="utf-8")
    assert panel.remembered_port(tower) is None
