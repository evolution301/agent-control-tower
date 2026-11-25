"""The 0.18 section, the feature overview and the language split must hold."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASES = "https://github.com/evolution301/agent-control-tower/releases"
CHINESE = "README.md"
ENGLISH = "README.en.md"


def _readme(name: str) -> str:
    return ROOT.joinpath(name).read_text(encoding="utf-8")


def test_chinese_is_the_landing_page_and_english_moved_next_to_it():
    zh = _readme(CHINESE)
    en = _readme(ENGLISH)
    assert "## 这是什么" in zh
    assert "## What this is" in en
    assert "(README.en.md)" in zh
    assert "(README.md)" in en
    assert not ROOT.joinpath("README.zh.md").exists()


def test_both_readmes_have_a_018_section():
    assert "## 0.18 有什么新东西" in _readme(CHINESE)
    assert "## What's new in 0.18" in _readme(ENGLISH)


def test_feature_overview_sits_after_the_before_after_list():
    zh = _readme(CHINESE)
    assert "## 它解决什么问题" in zh
    assert "## 功能全景" in zh
    assert "## 它是怎么工作的" in zh
    assert zh.index("## 它解决什么问题") < zh.index("## 功能全景") < zh.index("## 它是怎么工作的")
    en = _readme(ENGLISH)
    assert "## What it solves" in en
    assert "## What it does" in en
    assert "## How it works" in en
    assert en.index("## What it solves") < en.index("## What it does") < en.index("## How it works")


def test_both_readmes_describe_the_skill_library():
    for name in (CHINESE, ENGLISH):
        text = _readme(name)
        assert "tower skill save" in text
        assert "roles/<role>/skills/" in text


def test_the_018_section_names_the_session_pool_and_kickoff():
    for name in (CHINESE, ENGLISH):
        text = _readme(name)
        assert "pool_size" in text
        assert "--discuss" in text


def test_both_readmes_link_the_releases():
    for name in (CHINESE, ENGLISH):
        text = _readme(name)
        assert RELEASES in text
        assert "v0.18.0" in text
        assert "v0.18.5" in text


def test_both_command_tables_list_the_new_commands():
    for name in (CHINESE, ENGLISH):
        text = _readme(name)
        assert "tower kickoff" in text
        assert "tower dsh pool list" in text
        assert "tower review <task>" in text
        assert "tower notify test" in text
        assert "tower worker" in text
        assert "--concurrency" in text


def test_no_live_reference_to_the_deleted_chinese_copy():
    for path in (ROOT / "scripts" / "release.py", ROOT / CHINESE, ROOT / ENGLISH):
        assert "README.zh.md" not in path.read_text(encoding="utf-8")
