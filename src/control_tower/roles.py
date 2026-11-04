"""Add, remove and list roles (bots) without hand-editing tower.toml.

The tower is steered by conversation: a lead agent decides the team, then creates
one role per bot. Editing YAML by hand from an agent loop loses comments and is
easy to corrupt, so the edits here are line-based and surgical — the rest of the
file, including its comments and ordering, stays byte-identical.
"""

from __future__ import annotations

import re
from pathlib import Path

from .config import Role, TowerConfig, find_config_path, load_config

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
ROLE_LINE = re.compile(r"^  ([A-Za-z0-9][A-Za-z0-9._-]*):\s*$")
TOP_KEY = re.compile(r"^\S")
ADAPTERS_LINE = re.compile(r"^adapters:\s*$")
DSH_LINE = re.compile(r"^  dsh:\s*$")
SESSIONS_LINE = re.compile(r"^    sessions:\s*(\{\s*\})?\s*(#.*)?$")

def clean_text(value: str) -> str:
    """Normalise operator text before it reaches the config or the console.

    Charters are copied from editors that prefix a UTF-8 BOM and a markdown
    heading; printing those on a GBK console raises UnicodeEncodeError, and the
    BOM leaks into `description`. Strip both and collapse the whitespace.
    """
    text = (value or "").lstrip("\ufeff").strip()
    while text.startswith("#"):
        text = text[1:].lstrip()
    return " ".join(text.split())


DEFAULT_CHARTER = """# {name} · {description}

你负责「{description}」。

- 只在 {scopes} 内写文件；越界前先向上游工位提需求。
- 交付必须带证据：文件路径、命令输出、截图或可复现步骤，不允许只写"已完成"。
- 报告里写清：改了什么、依据是什么、下游要注意什么、还有什么没解决。
"""


def list_roles(cfg: TowerConfig) -> list[dict]:
    """One dict per configured role, with its wired session id when present."""
    sessions = (cfg.adapter_options.get("dsh") or {}).get("sessions") or {}
    out = []
    for name, role in cfg.roles.items():
        out.append(
            {
                "name": name,
                "description": clean_text(role.description),
                "adapter": role.adapter,
                "write_scopes": list(role.write_scopes),
                "max_rounds": role.max_rounds,
                "session_id": sessions.get(name, ""),
            }
        )
    return out


# ---------------------------------------------------------------- text editing


def _roles_span(lines: list[str]) -> tuple[int, int] | None:
    start = None
    for i, line in enumerate(lines):
        if line.rstrip("\r\n") == "roles:":
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if TOP_KEY.match(lines[j]):
            end = j
            break
    return start, end


def _role_span(lines: list[str], name: str) -> tuple[int, int] | None:
    span = _roles_span(lines)
    if span is None:
        return None
    start, end = span
    for i in range(start + 1, end):
        match = ROLE_LINE.match(lines[i])
        if match and match.group(1) == name:
            j = i + 1
            while j < end and not ROLE_LINE.match(lines[j]):
                j += 1
            return i, j
    return None


def _block(name: str, role: Role) -> list[str]:
    scopes = ", ".join(f'"{scope}"' for scope in role.write_scopes)
    return [
        f"  {name}:\n",
        f'    description: "{role.description}"\n',
        f"    adapter: {role.adapter}\n",
        f"    write_scopes: [{scopes}]\n",
        f"    max_rounds: {role.max_rounds}\n",
        f"    timeout_s: {role.timeout_s}\n",
    ]


def _insert_point(lines: list[str], span: tuple[int, int]) -> int:
    """End of the roles block, ignoring trailing blank lines."""
    _, end = span
    point = end
    while point > 0 and not lines[point - 1].strip():
        point -= 1
    return point


def _locate_sessions(lines: list[str]):
    """Find the `sessions:` line inside `adapters.dsh`.

    Returns (dsh_index, sessions_index, trailing_comment); indexes are None when
    that part of the tree does not exist yet. The search is scoped to the
    adapters block so a sibling adapter (openai, cli) never receives the entry,
    and a trailing comment on `sessions:` is preserved instead of being treated
    as a reason to append a second `adapters:` key.
    """
    adapters = None
    for i, line in enumerate(lines):
        if ADAPTERS_LINE.match(line):
            adapters = i
            break
    if adapters is None:
        return None, None, ""
    end = len(lines)
    for j in range(adapters + 1, len(lines)):
        if TOP_KEY.match(lines[j]):
            end = j
            break
    dsh = None
    for i in range(adapters + 1, end):
        if DSH_LINE.match(lines[i]):
            dsh = i
            break
    if dsh is None:
        return None, None, ""
    dsh_end = end
    for j in range(dsh + 1, end):
        if re.match(r"^  \S", lines[j]):
            dsh_end = j
            break
    for i in range(dsh + 1, dsh_end):
        match = SESSIONS_LINE.match(lines[i])
        if match:
            return dsh, i, (match.group(2) or "")
    return dsh, None, ""


def _set_session(lines: list[str], name: str, session_id: str) -> None:
    dsh, index, comment = _locate_sessions(lines)
    entry_text = '      %s: "%s"\n' % (name, session_id)
    if dsh is None and index is None:
        lines.append("\n")
        lines.append("adapters:\n")
        lines.append("  dsh:\n")
        lines.append("    sessions:\n")
        lines.append(entry_text)
        return
    if index is None:
        lines.insert(dsh + 1, "    sessions:\n")
        lines.insert(dsh + 2, entry_text)
        return
    stripped = lines[index].rstrip("\r\n").strip()
    if stripped.startswith("sessions:") and "{}" in stripped:
        suffix = ("  " + comment) if comment else ""
        lines[index] = "    sessions:" + suffix + "\n"
        lines.insert(index + 1, entry_text)
        return
    entry = re.compile(r"^\s{6}%s:" % re.escape(name))
    j = index + 1
    while j < len(lines) and (lines[j].startswith("      ") or not lines[j].strip()):
        if entry.match(lines[j]):
            lines[j] = entry_text
            return
        j += 1
    lines.insert(j, entry_text)


def _drop_session(lines: list[str], name: str) -> None:
    _, index, _ = _locate_sessions(lines)
    if index is None:
        return
    entry = re.compile(r"^\s{6}%s:" % re.escape(name))
    j = index + 1
    while j < len(lines) and (lines[j].startswith("      ") or not lines[j].strip()):
        if entry.match(lines[j]):
            del lines[j]
            return
        j += 1


# ---------------------------------------------------------------- public API


def add_role(
    config_path: Path,
    name: str,
    *,
    description: str = "",
    adapter: str = "dsh",
    write_scopes: list[str] | None = None,
    max_rounds: int = 2,
    timeout_s: int = 1800,
    session_id: str = "",
    charter: str = "",
    force: bool = False,
) -> dict:
    """Register one role (bot), write its charter and wire its session id."""
    if not NAME_RE.match(name):
        raise ValueError(f"invalid role name {name!r}: use letters, digits, '.', '_' or '-'")
    if adapter not in ("mock", "cli", "dsh", "openai"):
        raise ValueError(f"unknown adapter {adapter!r}: use mock | cli | dsh | openai")
    exists = _role_span(config_path.read_text(encoding="utf-8").splitlines(keepends=True), name) is not None
    if exists and not force:
        raise ValueError(f"role {name!r} already exists (pass --force to replace it)")

    role = Role(
        name=name,
        description=clean_text(description),
        adapter=adapter,
        write_scopes=list(write_scopes or []),
        max_rounds=int(max_rounds),
        timeout_s=int(timeout_s),
    )
    text = config_path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    block = _block(name, role)
    span = _roles_span(lines)
    if span is None:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append("\nroles:\n")
        lines.extend(block)
    else:
        existing = _role_span(lines, name)
        if existing is not None:
            lines[existing[0]:existing[1]] = block
        else:
            point = _insert_point(lines, span)
            lines[point:point] = block
    if session_id:
        _set_session(lines, name, session_id)
    config_path.write_text("".join(lines), encoding="utf-8")

    # charter
    role_dir = config_path.parent / "roles" / name
    role_dir.mkdir(parents=True, exist_ok=True)
    charter_path = role_dir / "AGENT.md"
    body = charter or DEFAULT_CHARTER.format(
        name=name,
        description=description or name,
        scopes=", ".join(role.write_scopes) or "你负责的目录",
    )
    charter_path.write_text(body, encoding="utf-8")
    return {
        "name": name,
        "replaced": exists,
        "session_id": session_id,
        "charter": str(charter_path),
        "write_scopes": list(role.write_scopes),
        "adapter": adapter,
    }


def remove_role(config_path: Path, name: str, *, purge: bool = False) -> dict:
    """Unregister a role (bot); --purge also deletes roles/<name>/."""
    lines = config_path.read_text(encoding="utf-8").splitlines(keepends=True)
    span = _role_span(lines, name)
    session_line = re.compile(r"^\s{6}%s:" % re.escape(name))
    if span is None and not any(session_line.match(line) for line in lines):
        raise ValueError(f"unknown role {name!r}")
    if span is not None:
        start, end = span
        while end > start and not lines[end - 1].strip():
            end -= 1
        del lines[start:end]
    _drop_session(lines, name)
    config_path.write_text("".join(lines), encoding="utf-8")

    removed_dir = None
    if purge:
        role_dir = config_path.parent / "roles" / name
        if role_dir.exists():
            for child in sorted(role_dir.rglob("*"), reverse=True):
                if child.is_file():
                    child.unlink()
                else:
                    child.rmdir()
            role_dir.rmdir()
            removed_dir = str(role_dir)
    return {"name": name, "purged": removed_dir}


def config_file(start: Path | str | None = None) -> Path:
    """The tower config file for a workspace (used by the CLI subcommands)."""
    return find_config_path(start)
