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

if os.name == "nt":
    try:
        import msvcrt as _msvcrt
    except ImportError:                 # a POSIX-shaped test host hides it
        _msvcrt = None
else:
    _msvcrt = None

if _msvcrt is None:
    try:
        import fcntl as _fcntl
    except ImportError:
        raise RuntimeError(
            "no file-locking backend: need msvcrt (Windows) or fcntl (POSIX)"
        ) from None
else:
    _fcntl = None


def _acquire(fh) -> None:
    # msvcrt locks one byte at the current position, and an unlock must name the
    # same byte — always the first one, so a file that grew keeps its lock valid.
    fh.seek(0)
    if _msvcrt is not None:
        _msvcrt.locking(fh.fileno(), _msvcrt.LK_NBLCK, 1)
    else:
        _fcntl.flock(fh.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)


def _release(fh) -> None:
    fh.seek(0)
    if _msvcrt is not None:
        _msvcrt.locking(fh.fileno(), _msvcrt.LK_UNLCK, 1)
    else:
        _fcntl.flock(fh.fileno(), _fcntl.LOCK_UN)


def try_lock(path):
    """Take `path`'s lock without waiting: the open handle, or None when held.

    The handle holds the lock until `release_lock`, so the caller must keep it
    alive for as long as it owns the file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+b")
    try:
        _acquire(fh)
    except OSError:
        fh.close()
        return None
    return fh


def release_lock(fh) -> None:
    try:
        _release(fh)
    finally:
        fh.close()


def tower_lock(cfg, timeout_s: float = 60.0, poll_s: float = 0.05):
    """Exclusive `.tower/.lock` for board-mutating sections.

    msvcrt on Windows, fcntl elsewhere; busy-waits up to `timeout_s` so a long
    adapter settle does not fail the next triage — it just waits its turn.
    """

    @contextlib.contextmanager
    def _locked() -> Iterator[None]:
        path = cfg.state_dir / ".lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(path, "a+b")
        deadline = time.time() + timeout_s
        try:
            while True:
                try:
                    _acquire(fh)
                    break
                except OSError:
                    if time.time() >= deadline:
                        raise TimeoutError("tower board lock held for over "
                                           f"{timeout_s:.0f}s — another process is stuck")
                    time.sleep(poll_s)
            try:
                yield
            finally:
                _release(fh)
        finally:
            fh.close()

    return _locked()
