"""Per-repo exclusive write lock.

Concurrent writers into the same chroma HNSW index corrupt it, so every write
path (`kb update`, `kb diary --update`, indexer --full/--incremental) holds an
exclusive, non-blocking `flock` on `knowledge/.kb.lock` for its duration.
The lock is re-entrant within one process (nested write paths share it).
"""
from __future__ import annotations

import fcntl
from contextlib import contextmanager
from pathlib import Path

LOCK_RELPATH = Path("knowledge") / ".kb.lock"

_held: dict[str, int] = {}  # lock path -> re-entrancy depth (this process)


class LockHeldError(RuntimeError):
    pass


@contextmanager
def write_lock(cfg):
    """Hold the per-repo write lock; raise LockHeldError at once if another
    process holds it."""
    root = Path(cfg["project_root"])
    path = root / LOCK_RELPATH
    key = str(path)
    if _held.get(key):
        _held[key] += 1
        try:
            yield
        finally:
            _held[key] -= 1
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+")
    try:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise LockHeldError(
                f"another kb update is running in {root} (lock: {path})"
            ) from None
        _held[key] = 1
        try:
            yield
        finally:
            _held.pop(key, None)
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    finally:
        fh.close()
