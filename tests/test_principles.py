"""The pstack-derived discipline: injected index, cards, CLI, config gate."""

import json

import pytest

from control_tower import cli
from control_tower.config import ensure_layout, parse_config
from control_tower.dispatch import render_prompt
from control_tower.principles import ORDER, PRINCIPLES, index_lines, show, scaffold_section
from control_tower.board import Board


@pytest.fixture()
def tower(tmp_path, monkeypatch):
    config = {
        "name": "pstack-tower",
        "workspace": str(tmp_path),
        "roles": {"seo": {"description": "s", "adapter": "mock"}},
    }
    path = tmp_path / "tower.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    cfg = parse_config(path)
    ensure_layout(cfg)
    monkeypatch.chdir(tmp_path)
    return cfg


def test_principle_cards_cover_the_curated_set():
    assert "prove-it-works" in PRINCIPLES and "understand-first" in PRINCIPLES
    assert ORDER == list(PRINCIPLES.keys()), "the index order is the curated order"
    for name in ORDER:
        assert PRINCIPLES[name]["card"] and PRINCIPLES[name]["applies"]


def test_scaffold_section_lists_every_principle_compactly():
    section = scaffold_section()
    for name in ORDER:
        assert name in section
    assert section.count("\n- ") <= len(ORDER) + 10, "compact: index only, not the cards"


def test_dispatch_prompt_injects_the_index(tower):
    board = Board(tower)
    task = board.create("seo", "work with discipline")
    board.save(task)
    prompt = render_prompt(tower, task)
    assert "Work discipline" in prompt
    assert "tower principles <name>" in prompt
    assert "prove-it-works — applies when" in prompt
    assert PRINCIPLES["prove-it-works"]["card"] not in prompt, "index only, no card bodies"


def test_config_can_turn_principles_off(tower, tmp_path):
    tower.principles = False
    board = Board(tower)
    task = board.create("seo", "no discipline injected")
    board.save(task)
    assert "Work discipline" not in render_prompt(tower, task)


def test_show_returns_the_full_card_and_rejects_unknown():
    card = show("smallest-change")
    assert card.startswith("# smallest-change") and "deletion" in card
    with pytest.raises(KeyError, match="no-such"):
        show("no-such")


def test_cli_lists_and_shows(tower, capsys):
    assert cli.main(["principles"]) == 0
    out = capsys.readouterr().out
    assert "understand-first" in out and "tower principles <name>" in out
    capsys.readouterr()
    assert cli.main(["principles", "fix-root-causes"]) == 0
    assert "root" in capsys.readouterr().out
    assert cli.main(["principles", "no-such"]) == 2


def test_config_field_defaults_on_and_parses_off(tower, tmp_path):
    assert tower.principles is True
    config = {
        "name": "off", "workspace": str(tmp_path / "w2"),
        "roles": {"seo": {"description": "s", "adapter": "mock"}},
        "principles": False,
    }
    path = tmp_path / "w2" / "tower.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(config), encoding="utf-8")
    assert parse_config(path).principles is False
