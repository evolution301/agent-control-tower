"""Tests for the one-shot machine wiring (`tower bootstrap`)."""

import json
import subprocess
import types

from control_tower import bootstrap as boot
from control_tower.config import load_config
from control_tower.roles import set_session

CONFIG = """name: p
workspace: .

roles:
  backend:
    description: server
    adapter: dsh
    write_scopes: ["src/"]
    max_rounds: 2

verify_commands:
  - "definitely-not-a-binary-zzz --version"

adapters:
  dsh:
    base_url: "http://127.0.0.1:1"
    secret: ""
    sessions: {}
    role_titles: { backend: "PRL 后端" }
"""


def _write_config(tmp_path):
    path = tmp_path / "tower.toml"
    path.write_text(CONFIG, encoding="utf-8")
    return path


def test_listening_ports_parses_netstat(tmp_path, monkeypatch):
    sample = """
  TCP    127.0.0.1:54408        0.0.0.0:0              LISTENING       15432
  TCP    127.0.0.1:54408        0.0.0.0:0              LISTENING       15432
  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING       880
  UDP    127.0.0.1:1900         *:*                                    1234
"""
    monkeypatch.setattr(
        boot.subprocess,
        "run",
        lambda *a, **k: types.SimpleNamespace(stdout=sample),
    )
    assert boot.listening_ports() == [135, 54408]


def test_detect_secret_reads_an_existing_bridge_script(tmp_path):
    bridge = tmp_path / "_dsh-extract"
    bridge.mkdir()
    (bridge / "notify_lead.py").write_text('SECRET_B64 = "aFTT3A_wbecgjFU"\n', encoding="utf-8")
    assert boot.detect_secret(tmp_path) == "aFTT3A_wbecgjFU"


def test_apply_connection_rewrites_base_url_and_secret(tmp_path):
    path = _write_config(tmp_path)
    assert boot.apply_connection(path, "http://127.0.0.1:54408", "abc123") == ["base_url", "secret"]
    text = path.read_text(encoding="utf-8")
    assert 'base_url: "http://127.0.0.1:54408"' in text
    assert 'secret: "abc123"' in text


def test_command_available_knows_builtins_and_paths(tmp_path):
    assert boot.command_available("echo ok") is True
    assert boot.command_available("definitely-not-a-binary-zzz --version") is False
    script = tmp_path / "check.py"
    script.write_text("print(1)", encoding="utf-8")
    assert boot.command_available('"%s" -c pass' % script) is True


def test_fix_verify_commands_drops_unrunnable_commands(tmp_path):
    path = _write_config(tmp_path)
    dropped = boot.fix_verify_commands(path)
    assert dropped == ["definitely-not-a-binary-zzz --version"]
    assert load_config(tmp_path).verify_commands == ["echo ok"]


def test_resolve_roles_pins_the_ids(tmp_path, monkeypatch):
    path = _write_config(tmp_path)

    class Stub:
        def resolve_session(self, request):
            return "session-backend-1", "title-match"

    monkeypatch.setattr(boot, "adapter_for", lambda *a, **k: Stub())
    rows, _adapter = boot.resolve_roles(path, secret="x", base_url="http://127.0.0.1:54408", pin=True)
    assert rows == [{"role": "backend", "session_id": "session-backend-1", "resolution": "title-match"}]
    cfg = load_config(tmp_path)
    assert (cfg.adapter_options["dsh"]["sessions"] or {})["backend"] == "session-backend-1"


def test_run_bootstrap_reports_a_missing_harness(tmp_path, monkeypatch):
    path = _write_config(tmp_path)
    monkeypatch.setattr(boot, "detect_secret", lambda workspace: "abc")
    monkeypatch.setattr(boot, "detect_base_url", lambda secret="", prefer="": (None, False))
    summary = boot.run_bootstrap(path, pin=False)
    assert summary["error"]
    assert summary["steps"][0]["step"] == "secret"


def test_run_bootstrap_end_to_end_with_stubs(tmp_path, monkeypatch):
    path = _write_config(tmp_path)

    class Stub:
        def resolve_session(self, request):
            return ("session-backend-1", "title-match") if request.role == "backend" else (None, "unresolved")

        def run(self, request):
            return types.SimpleNamespace(exit_code=0, text="summary: ok")

    monkeypatch.setattr(boot, "detect_secret", lambda workspace: "abc")
    monkeypatch.setattr(boot, "detect_base_url", lambda secret="", prefer="": ("http://127.0.0.1:54408", False))
    monkeypatch.setattr(boot, "adapter_for", lambda *a, **k: Stub())
    summary = boot.run_bootstrap(path, pin=True, smoke_role="backend")
    assert not summary.get("error")
    assert summary["base_url"] == "http://127.0.0.1:54408"
    assert summary["smoke"]["exit_code"] == 0
    assert load_config(tmp_path).verify_commands == ["echo ok"]
    assert json.dumps(summary)  # JSON-able
