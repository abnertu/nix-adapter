"""General Linux system administration and hardware inspection primitives.

Each helper that takes a `Native` runs its command through it and raises
`Conflict` as `Native.run` does when the command fails.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any, cast

from .exceptions import Conflict
from .io import atomic_write, directory_fd, regular_file
from .native import Native
from .systemd import key_values


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

    state = key_values(
        native.run(
            "timedatectl",
            "show",
            "--property=Timezone",
            "--property=LocalRTC",
        ).stdout
    )
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


@dataclass(frozen=True)
class _Allocation:
    """One public allocation and its original line position."""

    owner: str
    value: dict[str, int]
    index: int


def _valid_allocation_owner(owner: str) -> bool:
    return (
        bool(owner)
        and not owner.startswith(("#", "+", "-"))
        and all(
            character.isprintable() and not character.isspace() and character != ":"
            for character in owner
        )
    )


def _validate_subordinate_value(file: Path, value: dict[str, int]) -> None:
    start, count = value.get("start"), value.get("count")
    if (
        type(start) is not int
        or type(count) is not int
        or start <= 0
        or count <= 0
        or start + count - 1 > 4_294_967_294
    ):
        raise Conflict(f"invalid subordinate ID range in {file}")


def _parse_allocation(file: Path, line: str, index: int) -> _Allocation | None:
    if not line or line.startswith("#"):
        return None
    fields = line.split(":")
    if (
        len(line.encode("utf-8")) >= 1024
        or len(fields) != 3
        or not _valid_allocation_owner(fields[0])
        or not all(
            field.isascii() and field.isdecimal() and not field.startswith("0")
            for field in fields[1:]
        )
    ):
        raise Conflict(f"invalid subordinate ID allocation in {file}")
    try:
        value = {"start": int(fields[1]), "count": int(fields[2])}
    except ValueError:
        raise Conflict(f"invalid subordinate ID allocation in {file}") from None
    _validate_subordinate_value(file, value)
    return _Allocation(fields[0], value, index)


def _subordinate_file(file: Path) -> tuple[list[str], list[_Allocation]]:
    content = ""
    try:
        with (
            directory_fd(file.parent) as parent,
            regular_file(
                parent, file.name, encoding="utf-8", preserve_newlines=True
            ) as (
                stream,
                _,
            ),
        ):
            content = stream.read()
    except FileNotFoundError:
        pass
    except (OSError, UnicodeError):
        raise Conflict(f"cannot read subordinate ID allocations in {file}") from None
    if "\x00" in content:
        raise Conflict(f"NUL byte in subordinate ID allocations in {file}")
    lines = content.split("\n")
    if lines[-1] == "":
        lines.pop()
    allocations = [
        allocation
        for index, line in enumerate(lines)
        if (allocation := _parse_allocation(file, line, index)) is not None
    ]
    ordered = sorted(allocations, key=lambda allocation: allocation.value["start"])
    for left, right in pairwise(ordered):
        if ranges_overlap(left.value, right.value):
            raise Conflict(f"overlapping subordinate ID allocations in {file}")
    return lines, allocations


def _owner_aliases(file: Path, value: object) -> set[str]:
    if not isinstance(value, tuple):
        raise Conflict(f"invalid subordinate ID owner aliases for {file}")
    aliases: set[str] = set()
    for alias in cast("tuple[object, ...]", value):
        if not isinstance(alias, str) or not _valid_allocation_owner(alias):
            raise Conflict(f"invalid subordinate ID owner aliases for {file}")
        aliases.add(alias)
    return aliases


def _subordinate_state(
    file: Path, user: str, desired: dict[str, int], aliases: tuple[str, ...]
) -> tuple[list[str], _Allocation | None]:
    if not _valid_allocation_owner(user):
        raise Conflict(f"invalid subordinate ID account for {file}")
    owners = {user, *_owner_aliases(file, aliases)}
    _validate_subordinate_value(file, desired)
    lines, allocations = _subordinate_file(file)
    selected = [allocation for allocation in allocations if allocation.owner in owners]
    if len(selected) > 1:
        raise Conflict(
            f"multiple subordinate ID allocations exist for {user} in {file}"
        )
    owner = selected[0].owner if selected else user
    desired_line = f"{owner}:{desired['start']}:{desired['count']}"
    if len(desired_line.encode("utf-8")) >= 1024:
        raise Conflict(
            f"subordinate ID allocation exceeds the native record limit in {file}"
        )
    for allocation in allocations:
        if allocation.owner not in owners and ranges_overlap(desired, allocation.value):
            raise Conflict(
                f"desired subordinate ID range for {user} overlaps {allocation.owner} in {file}"
            )
    return lines, selected[0] if selected else None


def inspect_subordinate_range(
    path: Path | str,
    user: str,
    desired: dict[str, int],
    *,
    aliases: tuple[str, ...] = (),
) -> bool:
    """Return whether one allocation matches, without creating or changing files.

    Raise `Conflict` for unsafe paths, malformed or overlapping allocations,
    duplicate allocations for `user` and explicit owner aliases, or overlap
    with the desired range. Callers supply any numeric native identity aliases.
    """
    _, allocation = _subordinate_state(Path(path), user, desired, aliases)
    value = {"start": desired["start"], "count": desired["count"]}
    return allocation is not None and allocation.value == value


def ensure_subordinate_range(
    path: Path | str,
    user: str,
    desired: dict[str, int],
    uid: int | None = None,
    gid: int | None = None,
    *,
    aliases: tuple[str, ...] = (),
) -> None:
    """Give `user` the `desired` range in a subuid or subgid file.

    Raise `Conflict` for unsafe or conflicting allocations and as
    `atomic_write` does. Ownership defaults to the effective user and group.
    Explicit owner aliases identify the same account; preserve existing aliases.
    """
    file = Path(path)
    lines, allocation = _subordinate_state(file, user, desired, aliases)
    owner = allocation.owner if allocation is not None else user
    desired_line = f"{owner}:{desired['start']}:{desired['count']}"
    if allocation is None:
        lines.append(desired_line)
    else:
        lines[allocation.index] = desired_line
    target_uid = os.geteuid() if uid is None else uid
    target_gid = os.getegid() if gid is None else gid
    atomic_write(
        file,
        "\n".join(lines) + "\n",
        mode=0o644,
        uid=target_uid,
        gid=target_gid,
        encoding="utf-8",
        preserve_newlines=True,
    )
