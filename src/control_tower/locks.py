"""`.tower/.lock` — the cross-process board lock (design doc §2/§6).

Workers and CLI triage mutate the same task files; on Windows a crashed holder
must not wedge the tower forever, so the lock is advisory-but-mandatory for our
own code paths and released by process exit no matter what.
"""

from __future__ import annotations

import contextlib
import os
import time
from typing import Iterator


def tower_lock(cfg, timeout_s: float = 60.0, poll_s: float = 0.05):
    """Exclusive `.tower/.lock` for board-mutating sections.

    msvcrt on Windows, fcntl elsewhere; busy-waits up to `timeout_s` so a long
    adapter settle does not fail the next triage — it just waits its turn.
    """
    import msvcrt

    @contextlib.contextmanager
    def _locked() -> Iterator[None]:
        path = cfg.state_dir / ".lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(path, "a+b")
        deadline = time.time() + timeout_s
        try:
            while True:
                try:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.time() >= deadline:
                        raise TimeoutError("tower board lock held for over "
                                           f"{timeout_s:.0f}s — another process is stuck")
                    time.sleep(poll_s)
            try:
                yield
            finally:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            fh.close()

    return _locked()
