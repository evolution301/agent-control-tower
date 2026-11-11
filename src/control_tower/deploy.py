"""Deploy guard: never silently overwrite an online file that drifted.

2026-11-11 incident: a bot edited `wp-content/themes/generatepress-child/functions.php`
**on the server** (schema early-return + Microsoft Clarity block). The local working
copy never got those edits, so T-0102's normal "edit local → w.py upload" replaced
the online file and erased them. The server's pre-upload backup had to be used to
restore.

This module gives the tower a three-way check before any upload:

    local  vs  remote  vs  baseline(last sync)

* remote == local          → nothing to do
* remote == baseline       → only the local copy changed: **safe to upload**
* remote != baseline       → the server changed since the last sync: **REFUSE**;
                             `tower deploy pull` first (or merge deliberately)

`baseline` is recorded per (local, remote) pair in `.tower/deploy/<key>.json` and
updated by `init` / `pull` / `done`. Every action is ledgered; an optional `--role`
also appends a receipt under `roles/<role>/DEPLOY-SYNC.md` so the sync fact lives
where the bots read (the report asked for a sync marker under roles/).
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

from .board import now_ts
from .config import TowerConfig


class DeployError(Exception):
    """A check/done/pull refused to proceed — the message is the fix."""


# ---------------------------------------------------------------------------
# state
# ---------------------------------------------------------------------------

def deploy_dir(cfg: TowerConfig) -> Path:
    path = cfg.state_dir / "deploy"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _key(local: Path, remote: str) -> str:
    return hashlib.sha1(f"{local.resolve()}::{remote}".encode("utf-8")).hexdigest()[:16]


def _record_path(cfg: TowerConfig, local: Path, remote: str) -> Path:
    return deploy_dir(cfg) / f"{_key(local, remote)}.json"


def load_record(cfg: TowerConfig, local: Path, remote: str) -> dict[str, Any] | None:
    path = _record_path(cfg, local, remote)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_record(cfg: TowerConfig, record: dict[str, Any]) -> None:
    local = Path(record["local"])
    remote = str(record["remote"])
    path = _record_path(cfg, local, remote)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def normalize_bytes(raw: bytes) -> bytes:
    """Text-normalise before hashing: BOM and CRLF must not look like drift.

    The upload path itself may normalise line endings, so a byte-exact comparison
    would report 'drift' for the same content. We hash a normalised form and keep
    the byte size for the human report.
    """
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def digest(raw: bytes) -> str:
    return hashlib.sha256(normalize_bytes(raw)).hexdigest()[:16]


# ---------------------------------------------------------------------------
# remote fetch
# ---------------------------------------------------------------------------

def _render_command(spec: list[str] | str, cfg: TowerConfig, remote: str, local: Path) -> list[str] | str:
    mapping = {
        "remote": remote,
        "local": str(local),
        "workspace": str(cfg.workspace),
        "project_root": str(getattr(cfg, "project_root", cfg.workspace)),
        "python": sys.executable,
    }
    if isinstance(spec, str):
        return spec.format(**mapping)
    return [str(part).format(**mapping) for part in spec]


def fetch_remote(cfg: TowerConfig, remote: str, local: Path, fetch_cmd: str | list[str] | None = None) -> bytes:
    """Read the current server bytes for `remote`.

    The command comes from `--fetch-cmd` or `deploy.fetch` in tower.toml; `{remote}`,
    `{local}`, `{workspace}`, `{project_root}` and `{python}` are expanded. Its
    stdout is the file content (a trailing newline added by `print` is harmless:
    the digest normalises final whitespace-only line ending via text compare of
    content, see `digest`).
    """
    spec = fetch_cmd if fetch_cmd else (getattr(cfg, "deploy_fetch", None) or None)
    if not spec:
        raise DeployError(
            "no deploy.fetch configured — set `deploy.fetch` in tower.toml "
            "(e.g. [\"{python}\", \"-m\", \"control_tower.deploy_fetch\", \"--cwd\", "
            "\"<site>/generatepress-child\", \"--remote\", \"{remote}\"]) "
            "or pass --fetch-cmd")
    command = _render_command(spec, cfg, remote, local)
    shell = isinstance(command, str)
    try:
        proc = subprocess.run(
            command, shell=shell, capture_output=True, timeout=120,
            cwd=str(getattr(cfg, "project_root", cfg.workspace)),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DeployError(f"remote fetch failed to run: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or b"").decode("utf-8", "replace").strip()[:400]
        raise DeployError(f"remote fetch exited {proc.returncode}: {detail or '(no stderr)'}")
    return proc.stdout


# ---------------------------------------------------------------------------
# operations
# ---------------------------------------------------------------------------

def resolve_local(cfg: TowerConfig, local: str) -> Path:
    path = Path(local).expanduser()
    if not path.is_absolute():
        candidates = [Path.cwd() / path, Path(cfg.workspace) / path,
                      Path(getattr(cfg, "project_root", cfg.workspace)) / path]
        for candidate in candidates:
            if candidate.exists():
                return candidate.resolve()
        raise DeployError(f"local file not found: {local} (tried cwd, workspace, project_root)")
    if not path.exists():
        raise DeployError(f"local file not found: {local}")
    return path.resolve()


def check(cfg: TowerConfig, local_path: str, remote: str,
          fetch_cmd: str | list[str] | None = None) -> dict[str, Any]:
    local = resolve_local(cfg, local_path)
    local_raw = local.read_bytes()
    remote_raw = fetch_remote(cfg, remote, local, fetch_cmd)
    local_hash, remote_hash = digest(local_raw), digest(remote_raw)
    record = load_record(cfg, local, remote)
    baseline = (record or {}).get("baseline_sha")
    result: dict[str, Any] = {
        "local": str(local),
        "remote": remote,
        "local_bytes": len(local_raw),
        "remote_bytes": len(remote_raw),
        "local_sha": local_hash,
        "remote_sha": remote_hash,
        "baseline_sha": baseline,
        "recorded": bool(record),
    }
    if baseline is None:
        result.update(verdict="no_baseline", exit_code=2,
                      message=("no baseline for this file — run `tower deploy init` "
                               "(accept current server state) or `tower deploy pull` "
                               "(bring the server copy into local)"))
    elif remote_hash == local_hash:
        result.update(verdict="in_sync", exit_code=0,
                      message="local == remote: nothing to upload")
    elif remote_hash == baseline:
        result.update(verdict="local_changed", exit_code=0,
                      message=("only the local copy changed since the last sync: "
                               "safe to upload"))
    else:
        result.update(verdict="remote_drift", exit_code=1,
                      message=(f"REMOTE DRIFT: {remote} changed on the server since the "
                               f"last sync (baseline {baseline}, now {remote_hash}). "
                               "Do NOT upload — run `tower deploy pull` to bring the "
                               "server copy into local (local file is backed up first), "
                               "merge, then upload"))
    return result


def _backup_local(cfg: TowerConfig, local: Path) -> Path:
    import time

    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = deploy_dir(cfg) / "backup" / f"{stamp}-{local.name}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(local.read_bytes())
    return dest


def _receipt(cfg: TowerConfig, action: str, local: Path, remote: str,
             detail: dict[str, Any], role: str = "", note: str = "") -> Path:
    import time

    receipts = deploy_dir(cfg) / "receipts"
    receipts.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = receipts / f"{stamp}-{_key(local, remote)}-{action}.md"
    lines = [
        f"# deploy {action} · {now_ts()}", "",
        f"- local:  `{local}`",
        f"- remote: `{remote}`",
        f"- local_sha:  {detail.get('local_sha', '-')}",
        f"- remote_sha: {detail.get('remote_sha', '-')}",
        f"- baseline_sha: {detail.get('baseline_sha', '-')}",
        f"- verdict: {detail.get('verdict', action)}",
    ]
    if note:
        lines.append(f"- note: {note}")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    if role:
        role_dir = cfg.role_dir(role)
        role_dir.mkdir(parents=True, exist_ok=True)
        log = role_dir / "DEPLOY-SYNC.md"
        with log.open("a", encoding="utf-8") as fh:
            fh.write(f"- {now_ts()} {action} `{local.name}` → `{remote}` "
                     f"({detail.get('verdict', action)})"
                     + (f" — {note}" if note else "") + "\n")
    return path


def _update_baseline(cfg: TowerConfig, local: Path, remote: str, content: bytes,
                     note: str = "") -> dict[str, Any]:
    record = {
        "local": str(local),
        "remote": remote,
        "baseline_sha": digest(content),
        "baseline_bytes": len(content),
        "baseline_at": now_ts(),
        "note": note,
        "key": _key(local, remote),
    }
    save_record(cfg, record)
    return record


def pull(cfg: TowerConfig, local_path: str, remote: str,
         fetch_cmd: str | list[str] | None = None,
         role: str = "", note: str = "") -> dict[str, Any]:
    """Bring the server copy into the local working copy (local backed up first)."""
    local = resolve_local(cfg, local_path)
    remote_raw = fetch_remote(cfg, remote, local, fetch_cmd)
    backup = _backup_local(cfg, local)
    local.write_bytes(remote_raw)
    record = _update_baseline(cfg, local, remote, remote_raw, note=note or "pulled from server")
    detail = {"local_sha": digest(local.read_bytes()), "remote_sha": digest(remote_raw),
              "baseline_sha": record["baseline_sha"], "verdict": "pulled",
              "backup": str(backup)}
    receipt = _receipt(cfg, "pull", local, remote, detail, role=role, note=note)
    return {"ok": True, **detail, "receipt": str(receipt),
            "local_bytes": len(remote_raw)}


def init(cfg: TowerConfig, local_path: str, remote: str,
         fetch_cmd: str | list[str] | None = None,
         role: str = "", note: str = "") -> dict[str, Any]:
    """Accept the current server state as the baseline (no file change)."""
    local = resolve_local(cfg, local_path)
    remote_raw = fetch_remote(cfg, remote, local, fetch_cmd)
    record = _update_baseline(cfg, local, remote, remote_raw,
                              note=note or "baseline accepted (init)")
    detail = {"local_sha": digest(local.read_bytes()), "remote_sha": digest(remote_raw),
              "baseline_sha": record["baseline_sha"], "verdict": "initialised"}
    receipt = _receipt(cfg, "init", local, remote, detail, role=role, note=note)
    return {"ok": True, **detail, "receipt": str(receipt)}


def done(cfg: TowerConfig, local_path: str, remote: str,
         fetch_cmd: str | list[str] | None = None,
         role: str = "", note: str = "") -> dict[str, Any]:
    """After an upload: verify the server now matches local, then move the baseline."""
    result = check(cfg, local_path, remote, fetch_cmd)
    if result["verdict"] != "in_sync":
        raise DeployError(
            f"upload did not land: local {result['local_sha']} vs remote "
            f"{result['remote_sha']} ({result['verdict']}). Server content differs — "
            "check the upload command, then re-run `tower deploy check`")
    local = resolve_local(cfg, local_path)
    remote_raw = fetch_remote(cfg, remote, local, fetch_cmd)
    _update_baseline(cfg, local, remote, remote_raw, note=note or "upload verified")
    detail = {"local_sha": result["local_sha"], "remote_sha": result["remote_sha"],
              "baseline_sha": result["remote_sha"], "verdict": "uploaded"}
    receipt = _receipt(cfg, "done", local, remote, detail, role=role, note=note)
    return {"ok": True, **detail, "receipt": str(receipt)}


def status(cfg: TowerConfig) -> list[dict[str, Any]]:
    out = []
    for path in sorted(deploy_dir(cfg).glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        record["baseline_at"] = record.get("baseline_at", "")
        out.append(record)
    return out
