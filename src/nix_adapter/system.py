"""General Linux system administration and hardware inspection primitives.

Each helper that takes a `Native` runs its command through it and raises
`Conflict` as `Native.run` does when the command fails.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .exceptions import Conflict
from .io import atomic_write, read_managed
from .native import Native


def get_timezone(native: Native, root: Path | str = "/") -> str | None:
    """The timezone that timedatectl reports.

    Raise `Conflict` if `/etc/localtime` is not a symlink into the zoneinfo
    database, or if the RTC keeps local time.
    """
    root_path = Path(root)
    localtime_path = root_path / "etc/localtime"
    if localtime_path.exists() and not localtime_path.is_symlink():
        raise Conflict(f"{localtime_path} is not a zoneinfo symlink")
    if localtime_path.is_symlink():
        target = os.readlink(localtime_path)
        if not (
            target.startswith("/usr/share/zoneinfo/")
            or target.startswith("../usr/share/zoneinfo/")
        ) or ".." in target.removeprefix("../").split("/"):
            raise Conflict(
                f"{localtime_path} has an unexpected symlink target: {target}"
            )

    values = native.run(
        "timedatectl",
        "show",
        "--property=Timezone",
        "--property=LocalRTC",
    ).stdout
    state = dict(line.split("=", 1) for line in values.splitlines() if "=" in line)
    if state.get("LocalRTC") != "no":
        raise Conflict(
            "timedatectl reports LocalRTC; a local RTC needs an operator decision"
        )
    return state.get("Timezone")


def set_timezone(timezone: str, native: Native) -> None:
    """Set system timezone using timedatectl."""
    native.run("timedatectl", "set-timezone", timezone)


def get_hostname(native: Native, kind: str = "--static") -> str:
    """Get system hostname using hostnamectl."""
    return native.run("hostnamectl", kind).stdout.strip()


def set_hostname(hostname: str, native: Native, kind: str = "--static") -> None:
    """Set system hostname using hostnamectl."""
    native.run("hostnamectl", kind, "set-hostname", hostname)


def is_ntp_synchronized(native: Native) -> bool:
    """Check if network time synchronization is currently active."""
    output = native.run(
        "timedatectl", "show", "--property=NTPSynchronized", "--value"
    ).stdout.strip()
    return output == "yes"


def check_discard_support(native: Native) -> bool:
    """Return `True` if a mounted, unencrypted block device supports discard.

    Raise `Conflict` if a mounted device is encrypted, or if `lsblk` finds no
    mounted discard-capable device.
    """
    data = json.loads(
        native.run(
            "lsblk",
            "--json",
            "--bytes",
            "--output",
            "NAME,TYPE,DISC-MAX,MOUNTPOINTS",
        ).stdout
    )

    def walk(devices: list[dict[str, Any]], encrypted: bool = False) -> bool:
        suitable = False
        for device in devices:
            crypt = encrypted or device.get("type") == "crypt"
            mounted = any(device.get("mountpoints") or [])
            if mounted and crypt:
                raise Conflict(
                    f"encrypted mounted device {device.get('name')} needs a separate discard policy"
                )
            suitable |= mounted and int(device.get("disc-max") or 0) > 0
            suitable |= walk(device.get("children", []), crypt)
        return suitable

    devices = data.get("blockdevices", [])
    if not walk(devices):
        raise Conflict("lsblk found no mounted discard-capable device")
    return True


def ranges_overlap(left: dict[str, int], right: dict[str, int]) -> bool:
    """Return whether two [start, start + count) ranges overlap."""
    left_end = left["start"] + left["count"] - 1
    right_end = right["start"] + right["count"] - 1
    return left["start"] <= right_end and right["start"] <= left_end


def ensure_subordinate_range(
    path: Path | str,
    user: str,
    desired: dict[str, int],
    uid: int | None = None,
    gid: int | None = None,
) -> None:
    """Give `user` the `desired` range in a subuid or subgid file.

    Raise `Conflict` if the range overlaps another user's or the file is
    malformed, and as `atomic_write` does. Ownership defaults to the effective
    user and group.
    """
    file = Path(path)
    lines = read_managed(file).splitlines() if file.exists() else []
    user_indices: list[int] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = stripped.split(":")
        if len(fields) != 3 or not fields[1].isdigit() or not fields[2].isdigit():
            raise Conflict(f"invalid subordinate ID allocation in {file}")
        existing_user, start_text, count_text = fields
        if existing_user == user:
            user_indices.append(index)
            continue
        existing = {"start": int(start_text), "count": int(count_text)}
        if ranges_overlap(desired, existing):
            raise Conflict(
                f"desired subordinate ID range for {user} overlaps {existing_user} in {file}"
            )

    if len(user_indices) > 1:
        raise Conflict(
            f"multiple subordinate ID allocations exist for {user} in {file}"
        )
    desired_line = f"{user}:{desired['start']}:{desired['count']}"
    if user_indices:
        lines[user_indices[0]] = desired_line
    else:
        lines.append(desired_line)
    target_uid = os.geteuid() if uid is None else uid
    target_gid = os.getegid() if gid is None else gid
    atomic_write(
        file, "\n".join(lines) + "\n", mode=0o644, uid=target_uid, gid=target_gid
    )
