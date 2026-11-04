"""Tests for `tower plugin install` (no network: uses --source)."""

import json

import pytest

from control_tower import plugin_install


@pytest.fixture()
def dsh_home(tmp_path, monkeypatch):
    home = tmp_path / "dsh"
    profile = home / "profiles" / "desktop"
    profile.mkdir(parents=True)
    (profile / "package.json").write_text(
        json.dumps({"name": "p", "dependencies": {}, "dsh": {"profile": {"bundles": ["a"]}}}, indent=2),
        encoding="utf-8",
    )
    monkeypatch.setenv("DSH_HOME", str(home))
    return home


@pytest.fixture()
def bundle_source(tmp_path):
    source = tmp_path / "dsh-tower"
    (source / "test").mkdir(parents=True)
    (source / "index.js").write_text("export const name = 'dsh-tower'\n", encoding="utf-8")
    (source / "package.json").write_text(json.dumps({"name": "dsh-tower", "version": "9.9.9"}), encoding="utf-8")
    (source / "cordis.patch.yml").write_text("- insert: []\n", encoding="utf-8")
    (source / "test" / "sample.test.js").write_text("// test\n", encoding="utf-8")
    return source


def test_install_copies_files_and_registers_the_bundle(dsh_home, bundle_source):
    summary = plugin_install.install(profile="desktop", source=bundle_source)
    assert summary["version"] == "9.9.9"
    assert summary["bundle_added"] is True
    assert summary["restart_required"] is True

    profile = dsh_home / "profiles" / "desktop"
    installed = profile / "node_modules" / "dsh-tower"
    assert (installed / "index.js").exists()
    assert (installed / "cordis.patch.yml").exists()
    assert (installed / "test" / "sample.test.js").exists()

    manifest = json.loads((profile / "package.json").read_text(encoding="utf-8"))
    assert "dsh-tower" in manifest["dsh"]["profile"]["bundles"]
    assert manifest["dependencies"]["dsh-tower"] == "9.9.9"


def test_install_is_idempotent(dsh_home, bundle_source):
    plugin_install.install(source=bundle_source)
    second = plugin_install.install(source=bundle_source)
    assert second["bundle_added"] is False
    manifest = json.loads((dsh_home / "profiles" / "desktop" / "package.json").read_text(encoding="utf-8"))
    assert manifest["dsh"]["profile"]["bundles"].count("dsh-tower") == 1


def test_dry_run_changes_nothing(dsh_home, bundle_source):
    summary = plugin_install.install(source=bundle_source, dry_run=True)
    assert summary["dry_run"] is True
    assert not (dsh_home / "profiles" / "desktop" / "node_modules" / "dsh-tower").exists()


def test_missing_profile_is_reported(dsh_home):
    with pytest.raises(FileNotFoundError):
        plugin_install.install(profile="nope", source=dsh_home)


def test_bad_source_is_rejected(dsh_home, tmp_path):
    with pytest.raises(FileNotFoundError):
        plugin_install.install(source=tmp_path)
