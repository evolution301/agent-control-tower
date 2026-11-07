#!/usr/bin/env python3
"""Cut a release with one command, on Windows, macOS and Linux alike.

Write the CHANGELOG section first, then:

    python scripts/release.py --version 0.7.4

It will:
  1. refuse unless CHANGELOG.md already has a "## [0.7.4] - YYYY-MM-DD" section
  2. write the version into pyproject.toml, control_tower.__version__ and packages/dsh-tower/package.json
  3. run both suites for real and rewrite the README test-count badges with the real numbers
  4. run scripts/check-release-consistency.mjs --tag v0.7.4
  5. commit "chore(release): v0.7.4", tag it, push main and the tag

Pushing the tag lets the `release` GitHub workflow re-run the tests, build sdist + wheel and create the
GitHub Release from the changelog section.

Other modes:
    python scripts/release.py --badges-only              # only refresh the README test badges
    python scripts/release.py --version 0.7.4 --dry-run   # print the plan, touch nothing
    python scripts/release.py --version 0.7.4 --no-push   # commit and tag locally
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PYPROJECT = ROOT / "pyproject.toml"
INIT_PY = ROOT / "src" / "control_tower" / "__init__.py"
BUNDLE_PKG = ROOT / "packages" / "dsh-tower" / "package.json"
BUNDLE_INDEX = ROOT / "packages" / "dsh-tower" / "index.js"
CHANGELOG = ROOT / "CHANGELOG.md"
READMES = [ROOT / "README.md", ROOT / "README.zh.md"]
README_BADGE = re.compile(r"tests-\d+%20python%20%2B%20\d+%20node")
TRACKED = [
    "pyproject.toml",
    "CHANGELOG.md",
    "README.md",
    "README.zh.md",
    "src/control_tower/__init__.py",
    "packages/dsh-tower/package.json",
]


class Stop(Exception):
    """A release-stopping condition with a human-readable reason."""


def say(message: str = "") -> None:
    # A GBK console cannot encode the suites' output (✔, →); degrade, never crash.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass
    print(message, flush=True)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="")


def run(command: list[str], cwd: Path | None = None) -> tuple[int, str]:
    try:
        # encoding is mandatory: a GBK console cannot decode the suites' UTF-8
        # output (node --test prints the tool names' arrows and CJK), and the
        # reader thread would die mid-release.
        proc = subprocess.run(
            command,
            cwd=str(cwd or ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=900,
        )
    except FileNotFoundError as error:
        raise Stop(f"找不到命令 {command[0]}: {error}") from error
    except subprocess.TimeoutExpired as error:
        raise Stop(f"命令超时 {command[0]}: {error}") from error
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def require(command: list[str], cwd: Path | None = None, what: str = "") -> str:
    code, output = run(command, cwd)
    if code != 0:
        say(output)
        raise Stop(f"{what or ' '.join(command)} 失败（exit {code}）")
    return output


def current_version() -> str:
    match = re.search(r'(?m)^version = "([^"]+)"', read(PYPROJECT))
    if not match:
        raise Stop('pyproject.toml 里找不到 version = "..."')
    return match.group(1)


def changelog_section(version: str) -> str:
    escape = version.replace(".", r"\.")
    lines = read(CHANGELOG).splitlines()
    start = next((i for i, line in enumerate(lines) if re.match(rf"^## \[{escape}\]", line)), None)
    if start is None:
        raise Stop(
            f'CHANGELOG.md 里没有 "## [{version}] - 日期" 这一段，先写它再发版。\n\n'
            f"建议粘这一段（然后把下面几行改成这次真实做了什么）：\n"
            f"## [{version}] - {__import__('time').strftime('%Y-%m-%d')}\n\n### Added\n- ...\n\n### Fixed\n- ..."
        )
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"^## \[", lines[i])), len(lines))
    body = "\n".join(lines[start:end]).rstrip()
    if not body.strip():
        raise Stop(f'"## [{version}]" 段落是空的。')
    return body


def python_for_tests() -> str:
    candidates = [
        ROOT / ".venv" / "Scripts" / "python.exe",
        ROOT / ".venv" / "bin" / "python",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return sys.executable


def test_counts() -> tuple[int, int]:
    say("  pytest ...")
    output = require([python_for_tests(), "-m", "pytest", "-q"], what="pytest")
    py_match = re.search(r"(\d+) passed", output)
    python_count = int(py_match.group(1)) if py_match else 0

    say("  node --test ...")
    test_files = sorted(glob.glob(str(ROOT / "packages" / "dsh-tower" / "test" / "*.test.js")))
    if not test_files:
        raise Stop("packages/dsh-tower/test 下没有 *.test.js")
    output = require(["node", "--test", *test_files], what="node --test")
    node_match = re.search(r"(?m)^\s*(?:\u2139|#|i)\s*tests\s+(\d+)", output)
    node_count = int(node_match.group(1)) if node_match else 0

    say(f"  测试通过：pytest {python_count} · node {node_count}")
    return python_count, node_count


def update_badges(python_count: int, node_count: int, dry_run: bool) -> None:
    replacement = f"tests-{python_count}%20python%20%2B%20{node_count}%20node"
    for path in READMES:
        text = read(path)
        if not README_BADGE.search(text):
            say(f"  跳过（没有 tests 徽章）：{path.name}")
            continue
        if replacement in text:
            say(f"  已是最新：{path.name}")
            continue
        if dry_run:
            say(f"  [dry-run] 会改成 {replacement}：{path.name}")
            continue
        write(path, README_BADGE.sub(replacement, text))
        say(f"  徽章 → {replacement}（{path.name}）")


def set_version(from_version: str, to_version: str, dry_run: bool) -> None:
    edits = [
        (PYPROJECT, re.compile(r'(?m)^version = "[^"]+"'), f'version = "{to_version}"'),
        (INIT_PY, re.compile(r'__version__ = "[^"]+"'), f'__version__ = "{to_version}"'),
        (BUNDLE_PKG, re.compile(r'("version"\s*:\s*")[^"]+(")'), rf'\g<1>{to_version}\g<2>'),
        # the bundle's VERSION constant is pinned to package.json by a test
        (BUNDLE_INDEX, re.compile(r"(export const VERSION = ')([^']+)(')"), rf"\g<1>{to_version}\g<3>"),
    ]
    for path, pattern, replacement in edits:
        text = read(path)
        if not pattern.search(text):
            raise Stop(f"找不到要替换的版本号：{path}")
        if dry_run:
            say(f"  [dry-run] {path.name}: {from_version} → {to_version}")
            continue
        write(path, pattern.sub(replacement, text, count=1))
        say(f"  {path.name}: {from_version} → {to_version}")


def bundle_version() -> str:
    return json.loads(read(BUNDLE_PKG)).get("version", "")


def check_consistency(tag: str | None) -> None:
    command = ["node", "scripts/check-release-consistency.mjs"]
    if tag:
        command += ["--tag", tag]
    say(require(command, what="check-release-consistency").strip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bump, test, badge, commit, tag and push a release.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--version", help="the new version, e.g. 0.7.4")
    parser.add_argument("--badges-only", action="store_true", help="only refresh the README test badges")
    parser.add_argument("--dry-run", action="store_true", help="print the plan and touch nothing")
    parser.add_argument("--skip-tests", action="store_true", help="do not run the suites (and skip badge updates)")
    parser.add_argument("--no-push", action="store_true", help="commit and tag locally, do not push")
    args = parser.parse_args(argv)

    try:
        if args.badges_only:
            say("刷新 README 测试徽章")
            python_count, node_count = test_counts()
            update_badges(python_count, node_count, dry_run=False)
            say(f"\n完成。README 现在写的是 tests-{python_count}%20python%20%2B%20{node_count}%20node。")
            return 0

        if not args.version:
            raise Stop("缺 --version。用法：python scripts/release.py --version 0.7.4")
        if not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
            raise Stop(f'--version 要是 X.Y.Z 三段数字，收到 "{args.version}"。')

        old_version = current_version()
        if old_version == args.version:
            raise Stop(f"当前已经是 {args.version} 了。换一个新版本号，或先跑 --badges-only 刷徽章。")

        say(f"发布 v{args.version}（当前 {old_version}）")
        if args.dry_run:
            say("[dry-run] 只看计划，不写文件、不提交、不打 tag。")

        say("1/6 检查 CHANGELOG ...")
        notes = changelog_section(args.version)

        say("2/6 改版本号 ...")
        set_version(old_version, args.version, args.dry_run)

        say("3/6 跑测试 ...")
        counts: tuple[int, int] | None = None
        if args.skip_tests:
            say("  --skip-tests：跳过（不推荐，README 徽章也不会更新）")
        else:
            counts = test_counts()

        if counts:
            say("4/6 刷新 README 徽章 ...")
            update_badges(counts[0], counts[1], args.dry_run)

        say(f"5/6 一致性检查（tag v{args.version}）...")
        if args.dry_run:
            say("  [dry-run] 文件没真改，跳过（真实执行时会跑 check-release-consistency --tag）")
        else:
            check_consistency(f"v{args.version}")
        say("Release 正文（来自 CHANGELOG）：")
        say(notes)

        if args.dry_run:
            say("\n[dry-run] 计划完毕。真实执行时还会做：")
            say(f"  git add -- {' '.join(TRACKED)}")
            say(f'  git commit -m "chore(release): v{args.version}"')
            say(f'  git tag -a v{args.version} -m "v{args.version}"')
            say(f"  git push origin main; git push origin v{args.version}")
            return 0

        say("6/6 提交并推 tag ...")
        require(["git", "add", "--", *TRACKED], what="git add")
        status = require(["git", "status", "--short"], what="git status").strip()
        say(status or "  （没有可提交的改动）")
        require(["git", "commit", "-m", f"chore(release): v{args.version}"], what="git commit")
        require(["git", "tag", "-a", f"v{args.version}", "-m", f"v{args.version}"], what="git tag")
        say(f"  已提交并打 tag v{args.version}")

        if args.no_push:
            say("\n--no-push：都没推。要推就执行：")
            say(f"  git push origin main; git push origin v{args.version}")
            return 0

        require(["git", "push", "origin", "main"], what="git push main")
        require(["git", "push", "origin", f"v{args.version}"], what="git push tag")

        repo = "https://github.com/evolution301/agent-control-tower"
        say(f"\n已推送 v{args.version}。接下来是 GitHub 那边自动完成：")
        say(f"  Actions: {repo}/actions")
        say(f"  Release: {repo}/releases/tag/v{args.version}")
        say("  约 1–2 分钟后去看：Release 正文应和 CHANGELOG 那段一致，附件里应有 wheel 和 sdist。")
        return 0
    except Stop as stop:
        say(f"\n停止：{stop}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
