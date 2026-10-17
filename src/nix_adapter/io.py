"""High-security POSIX directory traversal and atomic file operations."""

from __future__ import annotations

import contextlib
import os
import secrets
import stat
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

from .exceptions import Conflict


@contextmanager
def directory_fd(path: Path, *, create: bool = False):
    """Walk absolute directories without following symlinks; hold each opened inode.

    All later reads, replacement and metadata operations are relative to this
    descriptor, so swapping an ancestor cannot redirect a privileged operation.
    """
    if not path.is_absolute() or ".." in path.parts:
        raise Conflict("managed paths must be absolute without traversal")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path.parts[1:]:
            if create:
                try:
                    os.mkdir(part, 0o755, dir_fd=fd)
                    os.fsync(fd)
                except FileExistsError:
                    pass
                except OSError as error:
                    raise Conflict(
                        f"failed to create directory {part}: {error}"
                    ) from None
            try:
                child = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=fd,
                )
            except FileNotFoundError:
                raise
            except OSError as error:
                raise Conflict(f"failed to open directory {part}: {error}") from None
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


@contextmanager
def regular_file(parent: int, name: str):
    fd = os.open(
        name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent
    )
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise Conflict("managed file has an unexpected type or hard links")
        with os.fdopen(fd, "r", closefd=False) as stream:
            yield stream, info
    finally:
        os.close(fd)


def read_managed(path: Path) -> str:
    try:
        with (
            directory_fd(path.parent) as parent,
            regular_file(parent, path.name) as (stream, _),
        ):
            return stream.read()
    except FileNotFoundError:
        return ""


def ensure_directory(path: Path, *, mode: int, uid: int, gid: int) -> None:
    with directory_fd(path, create=True) as fd:
        info = os.fstat(fd)
        if info.st_uid not in (os.geteuid(), uid) and os.geteuid() != 0:
            raise Conflict("managed directory has an unexpected owner")
        if (info.st_uid, info.st_gid) != (uid, gid):
            os.fchown(fd, uid, gid)
        if stat.S_IMODE(info.st_mode) != mode:
            os.fchmod(fd, mode)
        os.fsync(fd)


def remove_managed_file(
    path: Path, before_change: Callable[[], None] | None = None
) -> bool:
    try:
        with directory_fd(path.parent) as parent:
            with regular_file(parent, path.name):
                pass
            if before_change is not None:
                before_change()
            os.unlink(path.name, dir_fd=parent)
            os.fsync(parent)
            return True
    except FileNotFoundError:
        return False


def atomic_write(
    path: Path,
    content: str,
    *,
    mode: int = 0o644,
    uid: int = 0,
    gid: int = 0,
    before_change: Callable[[], None] | None = None,
) -> bool:
    with directory_fd(path.parent, create=True) as parent:
        try:
            with regular_file(parent, path.name) as (stream, current):
                if current.st_uid not in (os.geteuid(), uid) and os.geteuid() != 0:
                    raise Conflict("managed file has an unexpected owner")
                if (
                    stream.read() == content
                    and stat.S_IMODE(current.st_mode) == mode
                    and current.st_uid == uid
                    and current.st_gid == gid
                ):
                    return False
        except FileNotFoundError:
            pass
        if before_change is not None:
            before_change()
        temporary = f".nix-adapter-{secrets.token_hex(16)}"
        fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=parent,
        )
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(content)
                stream.flush()
                os.fchown(stream.fileno(), uid, gid)
                os.fchmod(stream.fileno(), mode)
                os.fsync(stream.fileno())
            os.replace(temporary, path.name, src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temporary, dir_fd=parent)
    return True
