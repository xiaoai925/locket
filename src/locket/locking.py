"""Reentrant repository locks shared by HTTP, MCP, and tenant provisioning.

The lock file lives outside the Git checkout and never enters a memory commit.
All service processes on the same host must use this gate; remote Git writers
still reconcile through normal PMF merges.
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

_gate = threading.Lock()
_entries: dict[str, tuple[threading.RLock, int]] = {}
_local = threading.local()


@contextmanager
def repository_lock(root: Path | str, timeout: float = 30):
    root = Path(root).resolve()
    key = os.path.normcase(str(root))
    with _gate:
        lock, users = _entries.get(key, (threading.RLock(), 0))
        _entries[key] = (lock, users + 1)
    acquired = False
    handle = None
    nested = False
    held = getattr(_local, "held", None)
    if held is None:
        held = _local.held = set()
    try:
        acquired = lock.acquire(timeout=timeout)
        if not acquired:
            raise TimeoutError("Memory repository busy")
        nested = key in held
        if not nested:
            folder = root.parent / ".locket-locks"
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            path = folder / (hashlib.sha256(key.encode()).hexdigest() + ".lock")
            handle = path.open("a+b")
            if path.stat().st_size == 0:
                handle.write(b"\0")
                handle.flush()
            deadline = time.monotonic() + timeout
            while True:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except (BlockingIOError, OSError):
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Memory repository busy") from None
                    time.sleep(0.025)
            held.add(key)
        yield
    finally:
        if handle is not None:
            if key in held:
                held.remove(key)
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()
        if acquired:
            lock.release()
        with _gate:
            _, users = _entries[key]
            if users == 1:
                del _entries[key]
            else:
                _entries[key] = (lock, users - 1)
