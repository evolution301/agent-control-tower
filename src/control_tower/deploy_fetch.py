"""Fetch one remote file through a project's `w.py` (WordPress sync bridge).

Run as a module so any tower can configure it without a wrapper script:

    python -m control_tower.deploy_fetch --cwd <site>/generatepress-child \\
        --remote wp-content/themes/generatepress-child/functions.php

`trans.php` addresses theme files **relative to the theme directory with a `./`
prefix** (w.py itself always sends `'./' + path`). Callers reasonably type the WP
path (`wp-content/themes/<theme>/functions.php`) or a bare name; both are
normalised here instead of coming back as `HTTP 400 Invalid path`
(2026-11-20 field report).

It inserts `--cwd` on `sys.path`, imports the project's `w`, calls
`w.sync_request({"action": "read_file", "path": "./<rel>"})` and writes the exact
file bytes to stdout (UTF-8). Errors go to stderr with a non-zero exit, so the
deploy guard reports them as a failed fetch instead of comparing garbage.
"""

from __future__ import annotations

import argparse
import os
import sys


def normalize_remote(cwd: str, remote: str, theme: str = "") -> str:
    """Turn any reasonable spelling into `./<path inside the theme dir>`.

    Accepted inputs (example with cwd=.../generatepress-child):
      ./functions.php                                      -> ./functions.php
      functions.php                                        -> ./functions.php
      wp-content/themes/generatepress-child/functions.php   -> ./functions.php
      G:/.../generatepress-child/functions.php             -> ./functions.php
      .\\inc\\custom.php                                     -> ./inc/custom.php
    """
    path = (remote or "").strip().replace("\\", "/")
    cwd_norm = os.path.abspath(cwd).replace("\\", "/").rstrip("/")
    theme_name = (theme or os.path.basename(cwd_norm)).strip("/")

    # an absolute path under the theme dir -> keep only the relative part
    if path.startswith(cwd_norm + "/"):
        path = path[len(cwd_norm) + 1:]
    elif len(path) > 1 and path[1] == ":":          # C:/... absolute elsewhere
        path = path.split("/", 3)[-1] if path.count("/") >= 3 else path
    path = path.lstrip("/")

    if path.startswith("./"):
        path = path[2:]
    # wp-content/themes/<theme>/<rel> -> <rel>, wherever the prefix sits
    # (also covers /html/wp-content/themes/<theme>/… and absolute forms)
    needle = f"wp-content/themes/{theme_name}/"
    idx = path.find(needle)
    if idx >= 0:
        path = path[idx + len(needle):]
    else:
        bare = f"wp-content/themes/{theme_name}"
        idx = path.find(bare)
        if idx >= 0:
            path = path[idx + len(bare):]
    path = path.strip("/")
    return f"./{path}" if path else ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tower deploy-fetch")
    parser.add_argument("--cwd", required=True,
                        help="directory holding the project's w.py (e.g. generatepress-child)")
    parser.add_argument("--remote", required=True,
                        help="remote path; relative to the theme dir (wp-content/themes/<theme>/… "
                             "is accepted and normalised to ./…)")
    parser.add_argument("--theme", default="",
                        help="theme directory name (default: basename of --cwd)")
    args = parser.parse_args(argv)

    remote = normalize_remote(args.cwd, args.remote, args.theme)
    if not remote:
        print("deploy_fetch: --remote resolved to an empty path", file=sys.stderr)
        return 2

    if args.cwd not in sys.path:
        sys.path.insert(0, args.cwd)
    try:
        import w  # type: ignore
    except Exception as exc:  # noqa: BLE001 - report whatever the import says
        print(f"deploy_fetch: cannot import w from {args.cwd!r}: {exc}", file=sys.stderr)
        return 3
    try:
        result = w.sync_request({"action": "read_file", "path": remote})
    except Exception as exc:  # noqa: BLE001 - network/auth errors are fetch failures
        print(f"deploy_fetch: read_file failed for {remote!r}: {exc}", file=sys.stderr)
        return 4
    data = (result or {}).get("data") or {}
    content = data.get("content")
    if content is None:
        print(f"deploy_fetch: response has no data.content for {remote!r}", file=sys.stderr)
        return 5
    sys.stdout.buffer.write(str(content).encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
