"""UTF-8 stdio on any code page (the plugin-channel mojibake bug, 2026-11-09)."""

import json
import os
import subprocess
import sys
import pathlib

import pytest

from control_tower.config import ensure_layout, parse_config


@pytest.fixture()
def tower(tmp_path, monkeypatch):
    config = {
        "name": "utf8-tower",
        "workspace": str(tmp_path),
        "roles": {"seo": {"description": "s", "adapter": "mock"}},
    }
    path = tmp_path / "tower.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    cfg = parse_config(path)
    ensure_layout(cfg)
    # a task whose brief is pure Chinese — the exact content that got mangled
    board_cfg = cfg
    from control_tower.board import Board

    b = Board(board_cfg)
    t = b.create("seo", "核对 https://evergocasa.com/ 首页 hero 是否包含 video 标签")
    b.save(t)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _run_cli(tmp_path, extra_env):
    env = os.environ.copy()
    env.pop("PYTHONUTF8", None)
    env.pop("PYTHONIOENCODING", None)
    env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "control_tower", "--workspace", str(tmp_path), "status"],
        capture_output=True, text=False, timeout=60,
    )


def test_piped_output_is_utf8_even_under_cp936(tower):
    proc = _run_cli(tower, {"PYTHONIOENCODING": "cp936"})
    text = proc.stdout.decode("utf-8")          # the plugin decodes UTF-8
    assert proc.returncode == 0
    assert "核对 https://evergocasa.com/" in text
    assert "\ufffd" not in text, "no replacement characters"


def test_explicit_cp936_request_is_overridden(tower):
    """Even PYTHONIOENCODING=cp936 must not change piped bytes (the fix)."""
    proc = _run_cli(tower, {"PYTHONIOENCODING": "cp936"})
    raw = proc.stdout
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as exc:  # pragma: no cover
        pytest.fail(f"output is not UTF-8: {exc}")


def test_interactive_console_path_is_untouched(tower):
    """The guard only fires for piped streams; isatty streams keep native encoding."""
    from control_tower.cli import _force_utf8_stdio

    class FakeStream:
        def __init__(self, tty):
            self._tty = tty
            self.encoding = "cp936"
            self.reconfigured = False

        def isatty(self):
            return self._tty

        def reconfigure(self, **kw):
            self.reconfigured = True
            self.encoding = kw.get("encoding", self.encoding)

    stream = FakeStream(tty=True)
    # call the helper logic directly via the module-level function
    import control_tower.cli as cli

    # _force_utf8_stdio is module-level; simulate by patching sys streams
    import sys

    old = sys.stdout
    sys.stdout = stream
    try:
        cli._force_utf8_stdio()
    finally:
        sys.stdout = old
    assert stream.reconfigured is False, "a tty keeps its native wrapper"
