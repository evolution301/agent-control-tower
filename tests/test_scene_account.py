"""Account label and the end-of-day cost projection."""

import json

import pytest

from control_tower.config import ensure_layout, parse_config
from control_tower.scene import build_scene


@pytest.fixture()
def tower(tmp_path):
    config = {
        "name": "acct-test",
        "workspace": str(tmp_path),
        "account": "https://x.com/yourhandle",
        "roles": {"ops": {"description": "ops", "adapter": "mock"}},
    }
    path = tmp_path / "tower.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    cfg = parse_config(path)
    ensure_layout(cfg)
    return cfg


def test_the_account_label_comes_from_tower_toml(tower):
    scene = build_scene(tower)
    assert scene["account"] == "https://x.com/yourhandle"


def test_an_empty_account_means_local_operator(tower, tmp_path):
    tower.account = ""
    assert build_scene(tower)["account"] == ""


def test_projected_cost_extends_today_over_the_day_fraction(tower):
    scene = build_scene(tower)
    fraction = scene["cost"]["day_fraction"]
    projected = scene["cost"]["projected_usd"]
    est = scene["cost"]["est_usd"]
    assert 0 < fraction <= 1
    assert projected == round(est / fraction, 2)
