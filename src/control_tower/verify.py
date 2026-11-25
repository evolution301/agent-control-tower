"""Verification: the lead must never accept a claim it has not checked.

Two layers:

* structural — the report has the required sections and non-empty checks
* command  — optional `verify.commands` from tower.toml are executed and must
  pass before a task can be accepted
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import re

from .board import REPORT_STATES, Task
from .config import TowerConfig, effective_review_roles


@dataclass
class Verification:
    ok: bool
    problems: list[str] = field(default_factory=list)
    ran: list[dict[str, Any]] = field(default_factory=list)
    deferred: bool = False
    reason: str = ""
    warnings: list[str] = field(default_factory=list)
    incomplete_report: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "problems": self.problems, "ran": self.ran,
                "deferred": self.deferred, "reason": self.reason,
                "warnings": self.warnings, "incomplete_report": self.incomplete_report}


def _expand_braces(text: str, limit: int = 64) -> list[str]:
    """Expand `{a,b}` groups the way a shell would, for file-existence checks.

    Only the first group is expanded per pass; unexpanded braces (or nonsense
    nesting) fall back to the literal string, so this can never make a real
    missing file look present.
    """
    out = [text]
    for _ in range(4):
        grown: list[str] = []
        changed = False
        for item in out:
            open_at = item.find("{")
            close_at = item.find("}", open_at + 1) if open_at >= 0 else -1
            if open_at < 0 or close_at < 0:
                grown.append(item)
                continue
            changed = True
            head, body, tail = item[:open_at], item[open_at + 1:close_at], item[close_at + 1:]
            grown.extend(head + option + tail for option in body.split(","))
            if len(grown) > limit:
                return [text]
        out = grown
        if not changed:
            break
    return out


_FILE_SPLIT = re.compile(r"[、,;，；|·•\t]+")
# A candidate must look like a path before it is stat'ed: a separator, or a
# filename ending in an extension. Prose bullets ("两份交付都在这两个路径",
# "无新增文件（仅修改）") are notes, not claims about files that should exist.
_PATH_SHAPE = re.compile(r"[\\/]|\.[A-Za-z][A-Za-z0-9]{0,7}$")


def _path_shaped(text: str) -> bool:
    return bool(_PATH_SHAPE.search(text))


# P1-D (2026-11-24): `expect:` in the brief front matter (or in the task's
# mandate, set by `dispatch --expect`) is the artifact fingerprint. A deliverable
# that exists and is non-empty outranks a missing REPORT.md: T-0240 shipped 8
# pages, 6 screenshots and a 9.4 KB stylesheet and was failed for the paperwork.
def _front_matter(text: str) -> dict[str, Any]:
    lines = (text or "").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    for index in range(1, len(lines)):
        if lines[index].strip() != "---":
            continue
        try:
            import yaml
            data = yaml.safe_load("\n".join(lines[1:index]))
        except Exception:  # noqa: BLE001 - malformed front matter is not a promise
            return {}
        return data if isinstance(data, dict) else {}
    return {}


def _expect_list(value: Any) -> list[str]:
    if isinstance(value, str):
        parts = re.split(r"[,\n;；、]+", value)
    elif isinstance(value, (list, tuple, set)):
        parts = [str(item) for item in value]
    else:
        return []
    out = []
    for part in parts:
        item = str(part).strip().strip("`'\"[]")
        if item:
            out.append(item)
    return out


def expect_paths(task: Task) -> list[str]:
    """The task's declared artifacts, from its mandate or the brief front matter."""
    mandate = getattr(task, "mandate", {}) or {}
    candidates = _expect_list(mandate.get("expect"))
    candidates += _expect_list(_front_matter(getattr(task, "brief", "")).get("expect"))
    out: list[str] = []
    for item in candidates:
        if item not in out:
            out.append(item)
    return out


def _non_empty(path: Path) -> bool:
    try:
        if path.is_dir():
            return any(path.iterdir())
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def artifact_problems(cfg: TowerConfig, task: Task, expected: list[str]) -> list[str]:
    """Every expected path must exist and carry content, under any known root."""
    roots = [Path(cfg.workspace)]
    project_root = getattr(cfg, "project_root", None)
    if project_root:
        roots.append(Path(project_root))
    raw_cwd = str(getattr(cfg, "verify_cwd", "") or "").strip()
    if raw_cwd:
        extra = Path(raw_cwd).expanduser()
        if not extra.is_absolute():
            extra = (Path(cfg.workspace) / extra).resolve()
        roots.append(extra)
    try:
        roots.append(Path(cfg.role_dir(task.role)))
    except (AttributeError, TypeError):
        pass

    problems: list[str] = []
    for token in expected:
        found = True
        for item in _expand_braces(token):
            path = Path(item)
            candidates = [path] if path.is_absolute() else [root / item for root in roots]
            if not any(_non_empty(candidate) for candidate in candidates):
                found = False
                break
        if not found:
            problems.append(f"expected path missing or empty: {token}")
    return problems


def split_file_candidates(line: str) -> list[str]:
    """B14: one report line may list several paths joined by 、 , ; | — split them.

    Each segment is checked on its own; a path with an annotation (` — why`) keeps
    the old behaviour of checking the path part only. A line carrying a brace
    pattern (`shots/{home,faq}.png`) is NOT split: its commas belong to the
    pattern, which `_expand_braces` handles. Segments that are prose rather than
    paths are dropped, so a note like "无新增文件（仅修改）" is not stat'ed and
    cannot reject an otherwise valid report.
    """
    base = line.split(" — ")[0].split(" - ")[0].strip().strip("`")
    if "{" in base and "}" in base and base.find("{") < base.find("}"):
        return [base] if _path_shaped(base) else []
    parts = [seg.strip().strip("`") for seg in _FILE_SPLIT.split(base)]
    return [part for part in parts if part and _path_shaped(part)]


def _fresh_report(cfg: TowerConfig, task: Task) -> dict[str, Any] | None:
    """B15: prefer the markdown report on disk over the cached copy in the task.

    A role (or the lead) may edit `<task>-REPORT.md` after filing; accepting must
    see those edits, not the stale parse stored in `.tower/tasks/T-….json`.
    """
    from .dispatch import parse_report

    candidates = []
    try:
        candidates.append(Path(cfg.role_dir(task.role)) / f"{task.id}-REPORT.md")
    except (AttributeError, TypeError):
        pass
    candidates.append(Path(cfg.workspace) / f"{task.id}-REPORT.md")
    project_root = getattr(cfg, "project_root", None)
    if project_root:
        candidates.append(Path(project_root) / f"{task.id}-REPORT.md")
    best: Path | None = None
    best_mtime = -1.0
    for candidate in candidates:
        try:
            mtime = candidate.stat().st_mtime
        except OSError:
            continue
        if mtime > best_mtime:
            best, best_mtime = candidate, mtime
    if best is None:
        return None
    try:
        text = best.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    parsed = parse_report(text)
    if not (parsed.get("state") or parsed.get("summary")
            or parsed.get("files") or parsed.get("checks")):
        return None
    return parsed


def _before(task: Task, since: str) -> bool:
    """Is the task older than `since` (ISO ts)? Unparseable input answers False."""
    from datetime import datetime

    try:
        boundary = datetime.strptime(since.strip(), "%Y-%m-%dT%H:%M:%S%z")
        created = datetime.strptime((task.created_at or "").strip(), "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return False
    return created < boundary


def _git(repo: Path, *args: str, timeout: float = 120):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )


def _repo_root(start: Path) -> Path | None:
    try:
        proc = _git(start, "rev-parse", "--show-toplevel")
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return Path(proc.stdout.strip())


# Changes under these paths belong to the tower itself (board, heartbeat, test
# caches), not to a lane: counting them would defer every second verify run.
_NOISE = ("__pycache__/", ".pytest_cache/", ".mypy_cache/", ".ruff_cache/",
          ".tower/", ".git/")


def _noise(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return any(marker in normalized for marker in _NOISE)


def _status_entries(repo: Path, untracked: bool = True) -> dict[str, str]:
    args = ["status", "--porcelain", "-z",
            "--untracked-files=all" if untracked else "--untracked-files=no"]
    try:
        proc = _git(repo, *args)
    except (OSError, subprocess.SubprocessError):
        return {}
    if proc.returncode != 0:
        return {}
    entries: dict[str, str] = {}
    records = proc.stdout.split("\0")
    index = 0
    while index < len(records):
        record = records[index]
        if len(record) > 3 and record[2] == " ":
            code, path = record[:2], record[3:]
            entries[path] = code
            if code[0] == "R" and index + 1 < len(records):
                entries[records[index + 1]] = "D "  # rename: drop the old path
                index += 1
        index += 1
    return entries


def _tree_signature(repo: Path) -> tuple:
    """Identity of the live tree, not just which paths are dirty: another lane
    rewriting an already-modified file keeps the same status letter."""
    signature = []
    for path, code in sorted(_status_entries(repo, untracked=False).items()):
        if _noise(path):
            continue
        try:
            stat = (repo / path).stat()
        except OSError:
            signature.append((path, code, None, None))
            continue
        signature.append((path, code, stat.st_mtime_ns, stat.st_size))
    return tuple(signature)


def _owned_files(cfg: TowerConfig, repo: Path, report: dict[str, Any]) -> set[str]:
    """Repo-relative paths this task claims — its own diff, as a set."""
    roots = [Path(cfg.workspace)]
    project_root = getattr(cfg, "project_root", None)
    if project_root:
        roots.append(Path(project_root))
    raw_cwd = str(getattr(cfg, "verify_cwd", "") or "").strip()
    if raw_cwd:
        extra = Path(raw_cwd).expanduser()
        if not extra.is_absolute():
            extra = (Path(cfg.workspace) / extra).resolve()
        roots.append(extra)
    owned: set[str] = set()
    repo = repo.resolve()
    for raw in report.get("files") or []:
        for candidate in split_file_candidates(str(raw)):
            for item in _expand_braces(candidate):
                path = Path(item)
                if not path.is_absolute():
                    found = next(
                        (root / item for root in roots if (root / item).exists()), None)
                    if found is None:
                        continue
                    path = found.resolve()
                try:
                    owned.add(path.resolve().relative_to(repo).as_posix())
                except ValueError:
                    continue  # outside this repo: not part of the isolated diff
    return owned


def _execute(commands: list[str], cwd: Path, isolated: bool = False) -> list[dict[str, Any]]:
    ran: list[dict[str, Any]] = []
    for cmd in commands:
        try:
            proc = subprocess.run(
                cmd, shell=True, cwd=str(cwd), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=900,
            )
            row = {"command": cmd, "cwd": str(cwd), "exit_code": proc.returncode,
                   "tail": (proc.stdout or proc.stderr or "")[-800:]}
            if isolated:
                row["isolated"] = True
            ran.append(row)
        except Exception as exc:
            ran.append({"command": cmd, "error": f"{type(exc).__name__}: {exc}"})
    return ran


def _failed(ran: list[dict[str, Any]]) -> bool:
    return any("error" in row or row.get("exit_code", 0) != 0 for row in ran)


def _command_problems(ran: list[dict[str, Any]]) -> list[str]:
    problems = []
    for row in ran:
        if "error" in row:
            problems.append(f"verify command error: {row['command']}")
            continue
        code = row.get("exit_code", 0)
        if code == 0:
            continue
        tail = (row.get("tail") or "").lower()
        if code == 127 or "not found" in tail or "not recognized" in tail:
            problems.append(
                f"verify command is not runnable from {row['cwd']} ({code}): "
                f"{row['command']} — likely a relative path; use an absolute one")
        else:
            problems.append(f"verify command failed ({code}): {row['command']}")
    return problems


def _run_isolated(repo: Path, verify_dir: Path,
                  commands: list[str]) -> list[dict[str, Any]] | None:
    """HEAD plus the whole uncommitted working set — what qa actually reviewed.

    Narrowing to the task's own files punished a task for a sibling edit it
    never touched (T-0030/T-0036: worker.py needed a constant from board.py).
    None when a worktree is not viable.
    """
    try:
        inside = verify_dir.resolve().relative_to(repo.resolve())
    except ValueError:
        return None
    tmp = Path(tempfile.mkdtemp(prefix="tower-verify-"))
    worktree = tmp / "wt"
    try:
        try:
            added = _git(repo, "worktree", "add", "--detach", str(worktree), "HEAD")
        except (OSError, subprocess.SubprocessError):
            return None
        if added.returncode != 0:
            return None
        for rel in _status_entries(repo):
            if _noise(rel):
                continue
            src, dst = repo / rel, worktree / rel
            if src.is_dir():
                shutil.copytree(src, dst, dirs_exist_ok=True)
            elif src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            elif dst.exists():
                dst.unlink()
        return _execute(commands, worktree / inside, isolated=True)
    finally:
        try:
            _git(repo, "worktree", "remove", "--force", str(worktree))
        except (OSError, subprocess.SubprocessError):
            pass
        shutil.rmtree(tmp, ignore_errors=True)


def _quiet_window() -> float:
    """Seconds the tree must stay unchanged before a live verify run.

    `TOWER_VERIFY_QUIET` (default 0 = run immediately) is the fallback knob for
    checkouts where a worktree is too heavy.
    """
    try:
        return max(0.0, float(os.environ.get("TOWER_VERIFY_QUIET", "0")))
    except ValueError:
        return 0.0


def _wait_quiet(repo: Path, window: float) -> None:
    if window <= 0:
        return
    end = time.monotonic() + window * 4
    last = _tree_signature(repo)
    stable_at = time.monotonic()
    while time.monotonic() < end:
        time.sleep(min(0.2, window))
        current = _tree_signature(repo)
        if current != last:
            last = current
            stable_at = time.monotonic()
        elif time.monotonic() - stable_at >= window:
            return


def _run_commands(cfg: TowerConfig, verify_dir: Path, report: dict[str, Any],
                  commands: list[str]) -> tuple[list[dict[str, Any]], bool, str]:
    """Run the checks where other lanes cannot perturb them.

    Dirty tree with edits this task does not claim → HEAD worktree carrying the
    whole uncommitted working set (the state qa reviewed), not only the task's
    files. If that is not viable, a live run whose failure coincides with a tree
    change is deferred, not a defect (and retried once).
    """
    if not commands:
        return [], False, ""
    repo = _repo_root(verify_dir)
    if repo is not None:
        owned = _owned_files(cfg, repo, report)
        foreign = [path for path in _status_entries(repo)
                   if path not in owned and not _noise(path)]
        if foreign:
            isolated = _run_isolated(repo, verify_dir, commands)
            if isolated is not None:
                return isolated, False, ""
    window = _quiet_window()
    if repo is not None:
        _wait_quiet(repo, window)
    before = _tree_signature(repo) if repo is not None else ()
    ran = _execute(commands, verify_dir)
    if repo is None or not _failed(ran):
        return ran, False, ""
    after = _tree_signature(repo)
    if after == before:
        return ran, False, ""
    _wait_quiet(repo, window)
    ran = _execute(commands, verify_dir)
    if not _failed(ran):
        return ran, False, ""
    if _tree_signature(repo) != after:
        return ran, True, "the tree changed during the run"
    return ran, False, ""


def review_gate(cfg: TowerConfig, task: Task) -> str:
    """Refuse acceptance until a configured reviewer has recorded a verdict.

    A tower with no effective review role keeps the old behaviour. A waiver is
    an explicit `review.waived_by` on the task, never a missing verdict.
    """
    if not effective_review_roles(cfg):
        return ""
    review = task.review or {}
    if str(review.get("waived_by") or "").strip():
        return ""
    verdict = str(review.get("verdict") or "").strip()
    if verdict and verdict != "pending":
        return ""
    return (f"task {task.id} has no qa verdict — run `tower review {task.id}` first "
            f"(or --no-review to waive)")


def verify_report(cfg: TowerConfig, task: Task, legacy: bool = False) -> Verification:
    problems: list[str] = []
    warnings: list[str] = []
    expected = expect_paths(task)
    if expected:
        problems.extend(artifact_problems(cfg, task, expected))
    # B15: the markdown file wins over the cached parse when it exists
    report = _fresh_report(cfg, task) or task.report or {}
    report_fields = ("state", "summary", "files", "checks", "self_review")
    incomplete = bool(expected) and not problems and not any(report.get(k) for k in report_fields)
    if incomplete:
        warnings.append(
            f"incomplete-report: {task.id} has no report, but its {len(expected)} expected "
            f"path(s) exist and are non-empty: {', '.join(expected[:5])}"
        )

    if not incomplete:
        requirements = {
            "summary": lambda r: bool(r.get("summary")),
            "files": lambda r: bool(r.get("files")),
            "checks": lambda r: bool(r.get("checks")),
        }

        # v0.2 — structured state, not prose. Borrowed from dsh-superagent: the lead
        # should be able to route on a closed vocabulary instead of reading paragraphs.
        state = (report.get("state") or "").strip().lower()
        if not state:
            problems.append("report is missing '## Status' (progress|waiting|ready|blocked|failed|dropped)")
        elif state not in REPORT_STATES:
            problems.append(f"unknown status '{state}' — expected one of {sorted(REPORT_STATES)}")
        elif state in ("blocked", "failed") and not (report.get("attention") or {}).get("question"):
            problems.append(f"status '{state}' requires one concrete question under '## Attention'")
        requirements["self_review"] = lambda r: bool((r.get("self_review") or "").strip())
        self_review_required = bool(getattr(cfg, "self_review", True)) and not legacy
        since = str(getattr(cfg, "self_review_since", "") or "").strip()
        if self_review_required and since and _before(task, since):
            self_review_required = False

        for need in cfg.accept_requires:
            if need == "self_review" and not self_review_required:
                continue
            check = requirements.get(need)
            if check is None:
                continue
            if not check(report):
                problems.append(f"report is missing '{need}'")

        # v0.10 — the self-review gate (pstack: 写完 ≠ 完成). When the tower turns it
        # on, a report without a substantive `## Self-review` line cannot pass, no
        # matter what accept_requires says.
        if self_review_required and not requirements["self_review"](report):
            problems.append(
                "report is missing '## Self-review' — re-read your own changes and fix "
                "issues BEFORE filing; one line on what you reviewed and fixed, or "
                "'no findings — checks re-run'")

        # A report that lists a check but never says what happened is not a check.
        weak = [c for c in report.get("checks", []) if "→" not in c and "->" not in c]
        if report.get("checks") and len(weak) == len(report.get("checks", [])):
            problems.append("every '## Checks' line lacks a '→ result' — ask for real output")

        # Files must exist inside the workspace or the project root. Roles routinely
        # write project deliverables into a sibling directory (the tower workspace is
        # the coordination root, not always the code root), so checking only the
        # workspace both rejects real files and lets a wrong-root write pass.
        roots = [cfg.workspace]
        project_root = getattr(cfg, "project_root", None)
        if project_root and Path(project_root) != Path(cfg.workspace):
            roots.append(Path(project_root))
        # If the checks run in the project, a report about that tree is about that
        # directory — resolve those paths there too.
        raw_verify_cwd = str(getattr(cfg, "verify_cwd", "") or "").strip()
        if raw_verify_cwd:
            extra = Path(raw_verify_cwd).expanduser()
            if not extra.is_absolute():
                extra = (Path(cfg.workspace) / extra).resolve()
            if extra not in roots:
                roots.append(extra)
        missing = []
        for f in report.get("files", []):
            candidates = split_file_candidates(str(f))
            if not candidates:
                continue
            for candidate in candidates:
                # A report often summarises a set with a shell-style brace pattern
                # (`shots/{home,faq}-{1440,390}.png`). Every expansion must exist: a
                # pattern that names files the role did not produce is still a false
                # claim, and an unexpandable string falls back to the literal path.
                expanded = _expand_braces(candidate)
                found = True
                for item in expanded:
                    path = Path(item)
                    if path.is_absolute():
                        if not path.exists():
                            found = False
                            break
                    elif not any((root / item).exists() for root in roots):
                        found = False
                        break
                if not found:
                    missing.append(candidate)
        if missing:
            problems.append("reported files do not exist (checked: "
                            + ", ".join(str(r) for r in roots) + "): " + ", ".join(missing[:5]))

    ran: list[dict[str, Any]] = []
    commands = list(getattr(cfg, "verify_commands", []) or [])
    # cwd for verify commands: `verify_cwd` when set (relative paths resolve
    # against the workspace), else the workspace. A relative script path plus the
    # workspace cwd is the classic "every accept fails with MODULE_NOT_FOUND".
    raw_cwd = str(getattr(cfg, "verify_cwd", "") or "").strip()
    if raw_cwd:
        verify_dir = Path(raw_cwd).expanduser()
        if not verify_dir.is_absolute():
            verify_dir = (Path(cfg.workspace) / verify_dir).resolve()
    else:
        verify_dir = Path(cfg.workspace)
    prior = len(problems)
    command_report = report
    if incomplete:
        # An isolated rerun must carry the artifacts that justify the settlement.
        command_report = {**report, "files": list(report.get("files") or []) + expected}
    ran, deferred, reason = _run_commands(cfg, verify_dir, command_report, commands)
    problems.extend(_command_problems(ran))
    if deferred and prior:
        deferred, reason = False, ""  # structural problems still mean rework
    if deferred:
        problems.append(f"verify-deferred: {reason}")

    return Verification(ok=not problems, problems=problems, ran=ran,
                        deferred=deferred, reason=reason, warnings=warnings,
                        incomplete_report=incomplete)
