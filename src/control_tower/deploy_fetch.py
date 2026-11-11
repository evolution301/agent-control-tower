"""Fetch one remote file through a project's `w.py` (WordPress sync bridge).

Run as a module so any tower can configure it without a wrapper script:

    python -m control_tower.deploy_fetch --cwd <site>/generatepress-child \\
        --remote wp-content/themes/generatepress-child/functions.php

It inserts `--cwd` on `sys.path`, imports the project's `w`, calls
`w.sync_request({"action": "read_file", "path": remote})` and writes the exact
file bytes to stdout (UTF-8). Errors go to stderr with a non-zero exit, so the
deploy guard reports them as a failed fetch instead of comparing garbage.
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tower deploy-fetch")
    parser.add_argument("--cwd", required=True,
                        help="directory holding the project's w.py (e.g. generatepress-child)")
    parser.add_argument("--remote", required=True,
                        help="remote path understood by w.sync_request read_file")
    args = parser.parse_args(argv)

    if args.cwd not in sys.path:
        sys.path.insert(0, args.cwd)
    try:
        import w  # type: ignore
    except Exception as exc:  # noqa: BLE001 - report whatever the import says
        print(f"deploy_fetch: cannot import w from {args.cwd!r}: {exc}", file=sys.stderr)
        return 3
    try:
        result = w.sync_request({"action": "read_file", "path": args.remote})
    except Exception as exc:  # noqa: BLE001 - network/auth errors are fetch failures
        print(f"deploy_fetch: read_file failed: {exc}", file=sys.stderr)
        return 4
    data = (result or {}).get("data") or {}
    content = data.get("content")
    if content is None:
        print("deploy_fetch: response has no data.content", file=sys.stderr)
        return 5
    sys.stdout.buffer.write(str(content).encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
