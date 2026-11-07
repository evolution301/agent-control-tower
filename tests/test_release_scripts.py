"""The release and collect entry points are scripts, so their contract is the exit code.

Neither script may touch the repo when it is only being asked to explain itself, and both must stop
with a readable reason instead of a traceback.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )


def test_release_without_arguments_stops_and_explains() -> None:
    proc = run(str(SCRIPTS / "release.py"))
    assert proc.returncode == 1
    assert "--version" in proc.stdout


def test_release_rejects_a_version_that_is_not_x_y_z() -> None:
    proc = run(str(SCRIPTS / "release.py"), "--version", "bad")
    assert proc.returncode == 1
    assert "X.Y.Z" in proc.stdout


def test_release_refuses_an_unwritten_changelog_section() -> None:
    proc = run(str(SCRIPTS / "release.py"), "--version", "99.99.99", "--dry-run")
    assert proc.returncode == 1
    assert "CHANGELOG.md" in proc.stdout
    assert not (ROOT / "pyproject.toml").read_text(encoding="utf-8").count('version = "99.99.99"')


def test_collect_script_documents_itself() -> None:
    proc = run(str(SCRIPTS / "collect_dsh_env.py"), "--help")
    assert proc.returncode == 0
    assert "--workspace" in proc.stdout
