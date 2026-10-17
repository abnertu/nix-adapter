"""Base service adapter for systemd service and identity reconciliation."""

from __future__ import annotations
import os
from pathlib import Path
import stat
from typing import Any

from .base import BaseAdapter
from .exceptions import Conflict
from .files import Files
from .native import Native


class BaseServiceAdapter(BaseAdapter):
    """Adapter specialized for single-service lifecycle, account, and unit file management."""

    def __init__(
        self,
        desired: dict[str, Any],
        root: Path | str = Path("/"),
        runner: Native | None = None,
        files: Files | None = None,
    ):
        super().__init__(desired=desired, root=root, runner=runner, files=files)

    def validate_file(self, key: str, mode_mask: int = 0o022) -> Path:
        path = self.path(key)
        if path.is_symlink() or not path.is_file():
            raise Conflict(f"path {key} must be a regular file")
        info = path.stat()
        if info.st_nlink != 1 or (info.st_uid != 0 and self.root == Path("/")):
            raise Conflict(f"path {key} has unsafe ownership")
        if stat.S_IMODE(info.st_mode) & mode_mask:
            raise Conflict(f"path {key} has unsafe permissions")
        return path

    def write_unit(
        self,
        path: Path,
        unit: str,
        receipt: Path,
        pending: Path,
        mode: int = 0o644,
    ) -> bool:
        """Write a systemd unit file atomically with receipt checking."""
        if not isinstance(unit, str):
            raise Conflict("unit content is invalid")
        if path.exists() and (
            path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1
        ):
            raise Conflict(f"unit {path.name} has conflicting type")
        if path.exists() and not receipt.exists() and path.read_text() != unit:
            raise Conflict(
                f"existing unit {path.name} requires removal before adoption"
            )
        if (
            path.exists()
            and path.read_text() == unit
            and stat.S_IMODE(path.stat().st_mode) == mode
        ):
            return False
        pending.touch()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.pending")
        temporary.write_text(unit)
        temporary.chmod(mode)
        os.replace(temporary, path)
        return True

    def ensure_metadata(self, path: Path, user: str, group: str, mode: int) -> None:
        """Ensure file or directory ownership and permissions."""
        info = path.stat()
        desired = f"{user}:{group}"
        if self.root == Path("/"):
            current = self.runner.run("stat", "-c", "%U:%G", str(path)).stdout.strip()
            if current != desired:
                self.runner.run("chown", desired, str(path))
        if stat.S_IMODE(info.st_mode) != mode:
            self.runner.run("chmod", f"{mode:04o}", str(path))

    def ensure_system_account(
        self,
        username: str,
        group: str | None = None,
        home: str | None = None,
        shell: str = "/usr/bin/nologin",
        create: bool = True,
    ) -> None:
        """Idempotently verify or create a dedicated system account and group."""
        group_name = group or username
        home_dir = home or f"/var/lib/{username}"

        group_res = self.runner.run("getent", "group", group_name, check=False)
        user_res = self.runner.run("getent", "passwd", username, check=False)

        if user_res.returncode == 0:
            if group_res.returncode != 0:
                raise Conflict(f"existing account {username} has no dedicated group")
            fields = user_res.stdout.strip().split(":")
            if len(fields) != 7 or fields[5:] != [home_dir, shell]:
                raise Conflict(f"existing account {username} has conflicting identity")
            primary = self.runner.run("id", "-gn", username).stdout.strip()
            if primary != group_name:
                raise Conflict(
                    f"existing account {username} has conflicting primary group"
                )
            return

        if not create:
            return

        if group_res.returncode != 0:
            self.runner.run("groupadd", "--system", group_name)

        self.runner.run(
            "useradd",
            "--system",
            "--gid",
            group_name,
            "--home-dir",
            home_dir,
            "--shell",
            shell,
            username,
        )
