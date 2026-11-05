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
    path: Path | str,
    *,
    timeout: float | None = 0,
    label: str = "operation",
):
    """Hold an exclusive lock on the file `path` for the `with` block.

    With `timeout` 0 the lock is tried once; with a positive `timeout` it is
    polled until then; with `None` it is polled without limit. Raise
    `Conflict` if another process holds it, or if the lock file is not a
    regular file owned by this user.
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
            raise Conflict(f"{label} lock {lock_path} has an unexpected owner or type")

        start_time = time.monotonic()
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if timeout is not None and (time.monotonic() - start_time) < timeout:
                    time.sleep(0.05)
                    continue
                raise Conflict(
                    f"another {label} is already running (lock {lock_path})"
                ) from None
        yield
    finally:
        os.close(descriptor)
