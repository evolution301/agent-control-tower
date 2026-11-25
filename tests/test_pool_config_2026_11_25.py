"""adapters.dsh.pool_size plumbing (T-0052)."""

import json
from pathlib import Path

import pytest

from control_tower.config import parse_config

ROOT = Path(__file__).resolve().parents[1]


def _cfg(tmp_path, pool_size="absent"):
    dsh = {"sessions": {}}
    if pool_size != "absent":
        dsh["pool_size"] = pool_size
    path = tmp_path / "tower.json"
    path.write_text(json.dumps({
        "name": "pool-config",
        "workspace": str(tmp_path / "tower"),
        "roles": {"core": {"adapter": "mock"}},
        "adapters": {"dsh": dsh},
    }), encoding="utf-8")
    return parse_config(path)


def test_default_is_off(tmp_path):
    cfg = _cfg(tmp_path)

    assert cfg.dsh_pool_size == 0
    assert cfg.dsh_pool_warning == ""


def test_zero_is_off(tmp_path):
    cfg = _cfg(tmp_path, 0)

    assert cfg.dsh_pool_size == 0
    assert cfg.dsh_pool_warning == ""


@pytest.mark.parametrize("size", [1, 4, 16, 17, 64])
def test_valid_sizes_are_kept(tmp_path, size):
    cfg = _cfg(tmp_path, size)

    assert cfg.dsh_pool_size == size
    assert cfg.dsh_pool_warning == ""


def test_negative_falls_back_to_zero(tmp_path):
    cfg = _cfg(tmp_path, -1)

    assert cfg.dsh_pool_size == 0
    assert "0..64" in cfg.dsh_pool_warning


@pytest.mark.parametrize("value", ["many", 3.5, True])
def test_non_integer_falls_back_to_zero(tmp_path, value):
    cfg = _cfg(tmp_path, value)

    assert cfg.dsh_pool_size == 0
    assert "must be an integer" in cfg.dsh_pool_warning


def test_above_hard_cap_falls_back_to_zero(tmp_path):
    cfg = _cfg(tmp_path, 99)

    assert cfg.dsh_pool_size == 0
    assert "0..64" in cfg.dsh_pool_warning


def test_quickstart_documents_the_pool_and_boot_caveat():
    text = " ".join((ROOT / "docs" / "dsh-quickstart.md").read_text(encoding="utf-8").split())

    assert "pool_size" in text
    assert "once per boot" in text
    assert "next bootstrap" in text
