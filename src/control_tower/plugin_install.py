"""Install the `dsh-tower` bundle into a DSH profile (one command).

The desktop app owns its profile - `dsh plugin --profile desktop ...` refuses to
touch it - so the bundle has to be dropped into the profile's `node_modules` and
listed in `dsh.profile.bundles` by hand. This does exactly that, from a local
checkout (`--source`) or from the published repository.
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

BUNDLE_NAME = "dsh-tower"
REPO_RAW = "https://raw.githubusercontent.com/evolution301/agent-control-tower/main/packages/dsh-tower"
BUNDLE_FILES = ("index.js", "package.json", "cordis.patch.yml", "README.md", "lib/client.js")


def dsh_home() -> Path:
    override = os.environ.get("DSH_HOME", "").strip()
    return Path(override) if override else Path.home() / ".dsh"


def profile_dir(profile: str = "desktop") -> Path:
    return dsh_home() / "profiles" / profile


def _download(url: str, timeout: float = 30) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "agent-control-tower"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # pragma: no cover - network
        return response.read()


def _collect(source: str | Path | None) -> dict[str, bytes]:
    """Read the bundle's files from a local checkout or the repository."""
    files: dict[str, bytes] = {}
    if source:
        root = Path(source)
        for name in BUNDLE_FILES:
            path = root / name
            if path.exists():
                files[name] = path.read_bytes()
        if "index.js" not in files:
            raise FileNotFoundError("%s does not look like packages/dsh-tower (no index.js)" % root)
        return files
    for name in BUNDLE_FILES:
        files[name] = _download("%s/%s" % (REPO_RAW, name))
    return files


def install(
    profile: str = "desktop",
    source: str | Path | None = None,
    dry_run: bool = False,
) -> dict:
    """Write the bundle into <profile>/node_modules/dsh-tower and register it."""
    target_profile = profile_dir(profile)
    manifest_path = target_profile / "package.json"
    if not manifest_path.exists():
        raise FileNotFoundError("no profile at %s (open the DSH desktop app once first)" % target_profile)

    files = _collect(source)
    bundle_manifest = json.loads(files["package.json"].decode("utf-8"))
    version = str(bundle_manifest.get("version") or "0.0.0")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    profile_meta = manifest.setdefault("dsh", {}).setdefault("profile", {})
    bundles = list(profile_meta.get("bundles") or [])
    added_bundle = BUNDLE_NAME not in bundles
    if added_bundle:
        bundles.append(BUNDLE_NAME)
    profile_meta["bundles"] = bundles
    dependencies = manifest.setdefault("dependencies", {})
    previous = dependencies.get(BUNDLE_NAME)
    dependencies[BUNDLE_NAME] = version

    summary = {
        "profile": str(target_profile),
        "version": version,
        "previous_version": previous,
        "bundle_added": added_bundle,
        "files": sorted(files),
        "dry_run": dry_run,
    }
    if dry_run:
        return summary

    target = target_profile / "node_modules" / BUNDLE_NAME
    target.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (target / name).write_bytes(data)
    # tests live next to the bundle in a checkout; copy them when available
    if source:
        tests = Path(source) / "test"
        if tests.is_dir():
            (target / "test").mkdir(exist_ok=True)
            for test_file in tests.glob("*.test.js"):
                (target / "test" / test_file.name).write_bytes(test_file.read_bytes())
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary["installed_to"] = str(target)
    summary["restart_required"] = True
    return summary
