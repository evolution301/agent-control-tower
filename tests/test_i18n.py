"""The panel speaks Chinese and English (`?lang=zh|en`, default from Accept-Language).

The layout is Chinese-primary (it is the 值守台HQ look the user asked for); the
*labels* — the words the JS writes at runtime — come from the server, so the
language switch can never rewrite code.
"""

from types import SimpleNamespace

from control_tower.panel import pick_lang, render_page


def cfg():
    return SimpleNamespace(name="prl-ops", workspace="C:/work")


def test_query_wins_over_accept_language():
    assert pick_lang("lang=zh", "en-US") == "zh"
    assert pick_lang("lang=en", "zh-CN") == "en"


def test_default_follows_accept_language():
    assert pick_lang("", "zh-CN,zh;q=0.9,en;q=0.8") == "zh"
    assert pick_lang("", "en-US,en;q=0.9") == "en"
    assert pick_lang("", "") == "en"


def test_chinese_page_has_chinese_labels():
    page = render_page(cfg(), "zh")
    for word in ("需要你处理", "进行中", "角色 — 谁在干什么", "操作日志", "加载中…", "需要你"):
        assert word in page, word
    assert '"needs_you": "需要你"' in page, "runtime labels are Chinese"
    assert "nothing in flight" not in page


def test_english_page_switch_carries_english_runtime_labels():
    page = render_page(cfg(), "en")
    for word in ("Needs you", "In flight"):   # present in the L labels and headings
        assert word.lower() in page.lower(), word
    assert '"needs_you": "needs you"' in page
    assert "正在跑" not in page


def test_page_offers_the_language_toggle():
    for lang in ("zh", "en"):
        page = render_page(cfg(), lang)
        assert 'href="?lang=zh"' in page
        assert 'href="?lang=en"' in page
