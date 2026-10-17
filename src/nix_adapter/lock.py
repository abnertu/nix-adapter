"""Inter-process locking operations."""

from __future__ import annotations

import fcntl
import os
import stat
import time
from contextlib import contextmanager
from pathlib import Path

from .exceptions import Conflict


@contextmanager
def operation_lock(
    path: Path | str = Path("/run/lock/nix-config.lock"),
    *,
    timeout: float | None = 0,
    label: str = "operation",
):
    """Serialize mutations across processes using an exclusive file lock.

    If timeout is 0 (default), non-blocking lock is attempted. If timeout > 0, polls
    until lock is acquired or timeout expires.
    """
    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600
    )
    try:
        entry = os.fstat(descriptor)
        if not stat.S_ISREG(entry.st_mode) or (
            entry.st_uid != os.geteuid() and os.geteuid() != 0
        ):
            raise Conflict(f"{label} lock has an unexpected owner or type")

        start_time = time.monotonic()
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if timeout is not None and (time.monotonic() - start_time) < timeout:
                    time.sleep(0.05)
                    continue
                raise Conflict(f"another {label} is already running") from None
        yield
    finally:
        os.close(descriptor)
