"""Deploy guard: never overwrite an online file that drifted (2026-11-11 incident)."""

import json
import pathlib
import sys

import pytest

from control_tower import cli
from control_tower import deploy as deploy_mod
from control_tower.config import ensure_layout, parse_config

FETCH = [sys.executable, "-c",
         "import sys;sys.stdout.buffer.write(open(sys.argv[1],'rb').read())", "{remote}"]


def _tower(tmp_path, monkeypatch):
    config = {
        "name": "deploy-tower",
        "workspace": str(tmp_path),
        "roles": {"frontend": {"description": "f", "adapter": "mock"}},
        "deploy": {"fetch": FETCH},
    }
    path = tmp_path / "tower.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    cfg = parse_config(path)
    ensure_layout(cfg)
    monkeypatch.chdir(tmp_path)
    return cfg


def _files(tmp_path, remote_text, local_text):
    remote = tmp_path / "server" / "functions.php"
    remote.parent.mkdir(parents=True, exist_ok=True)
    remote.write_text(remote_text, encoding="utf-8")
    local = tmp_path / "generatepress-child" / "functions.php"
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text(local_text, encoding="utf-8")
    return local, remote


def test_check_without_baseline_tells_you_what_to_run(tmp_path, monkeypatch):
    cfg = _tower(tmp_path, monkeypatch)
    local, remote = _files(tmp_path, "server", "local")
    result = deploy_mod.check(cfg, str(local), str(remote))
    assert result["verdict"] == "no_baseline" and result["exit_code"] == 2
    assert "deploy init" in result["message"] and "deploy pull" in result["message"]


def test_local_change_only_is_safe_but_remote_drift_is_refused(tmp_path, monkeypatch):
    cfg = _tower(tmp_path, monkeypatch)
    local, remote = _files(tmp_path, "server-v1", "local-v1")
    deploy_mod.init(cfg, str(local), str(remote))

    # only the local copy moved: upload is the intended action
    local.write_text("local-v2", encoding="utf-8")
    assert deploy_mod.check(cfg, str(local), str(remote))["verdict"] == "local_changed"

    # the server moved underneath us: uploading now would erase that edit
    remote.write_text("server-v2-with-clarity", encoding="utf-8")
    result = deploy_mod.check(cfg, str(local), str(remote))
    assert result["verdict"] == "remote_drift" and result["exit_code"] == 1
    assert "REMOTE DRIFT" in result["message"] and "deploy pull" in result["message"]


def test_in_sync_after_pull_and_pull_backs_up_the_local_copy(tmp_path, monkeypatch):
    cfg = _tower(tmp_path, monkeypatch)
    local, remote = _files(tmp_path, "server-v1", "local-v1")
    deploy_mod.init(cfg, str(local), str(remote))
    remote.write_text("server-v2", encoding="utf-8")

    result = deploy_mod.pull(cfg, str(local), str(remote), role="frontend")
    assert result["ok"] and local.read_text(encoding="utf-8") == "server-v2"
    backups = list((cfg.state_dir / "deploy" / "backup").glob("*functions.php"))
    assert backups and backups[0].read_text(encoding="utf-8") == "local-v1"
    assert deploy_mod.check(cfg, str(local), str(remote))["verdict"] == "in_sync"
    # receipt + role-side marker exist
    assert (cfg.role_dir("frontend") / "DEPLOY-SYNC.md").exists()
    assert list((cfg.state_dir / "deploy" / "receipts").glob("*.md"))


def test_done_verifies_the_upload_landed_and_moves_the_baseline(tmp_path, monkeypatch):
    cfg = _tower(tmp_path, monkeypatch)
    local, remote = _files(tmp_path, "server-v1", "local-v1")
    deploy_mod.init(cfg, str(local), str(remote))

    # upload did NOT land: server still differs from local
    with pytest.raises(deploy_mod.DeployError, match="did not land"):
        deploy_mod.done(cfg, str(local), str(remote))

    # a correct upload makes the server equal to local
    remote.write_text("local-v1", encoding="utf-8")
    result = deploy_mod.done(cfg, str(local), str(remote))
    assert result["verdict"] == "uploaded"
    assert deploy_mod.load_record(cfg, local, str(remote))["baseline_sha"] == result["remote_sha"]


def test_crlf_and_bom_are_not_drift(tmp_path, monkeypatch):
    cfg = _tower(tmp_path, monkeypatch)
    local, remote = _files(tmp_path, "line1\nline2\n", "line1\nline2\n")
    deploy_mod.init(cfg, str(local), str(remote))
    remote.write_bytes(b"\xef\xbb\xbfline1\r\nline2\r\n")
    assert deploy_mod.check(cfg, str(local), str(remote))["verdict"] == "in_sync"


def test_render_command_expands_placeholders(tmp_path, monkeypatch):
    cfg = _tower(tmp_path, monkeypatch)
    local, remote = _files(tmp_path, "s", "l")
    rendered = deploy_mod._render_command(
        ["x", "{remote}", "{local}", "{python}", "{project_root}"], cfg, str(remote), local)
    assert rendered[1] == str(remote) and rendered[2] == str(local)
    assert rendered[3] == sys.executable
    assert rendered[4] == str(cfg.project_root)


def test_cli_exit_codes_and_status(tmp_path, monkeypatch, capsys):
    cfg = _tower(tmp_path, monkeypatch)
    local, remote = _files(tmp_path, "server", "local")
    assert cli.main(["deploy", "check", str(local), "--remote", str(remote)]) == 2
    assert cli.main(["deploy", "init", str(local), "--remote", str(remote)]) == 0
    assert cli.main(["deploy", "check", str(local), "--remote", str(remote)]) == 0
    remote.write_text("server-changed", encoding="utf-8")
    assert cli.main(["deploy", "check", str(local), "--remote", str(remote)]) == 1
    out = capsys.readouterr().out
    assert "REMOTE DRIFT" in out
    assert cli.main(["deploy", "status"]) == 0
    assert str(remote) in capsys.readouterr().out
