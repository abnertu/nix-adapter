"""General Linux system administration and hardware inspection primitives."""

from __future__ import annotations
import json
import os
from pathlib import Path

from .exceptions import Conflict
from .native import Native


def get_timezone(runner: Native | None = None, root: Path | str = "/") -> str | None:
    """Read timezone from /etc/localtime and timedatectl."""
    root_path = Path(root)
    localtime_path = root_path / "etc/localtime"
    if localtime_path.exists() and not localtime_path.is_symlink():
        raise Conflict("localtime is not a zoneinfo symlink")
    if localtime_path.is_symlink():
        target = os.readlink(localtime_path)
        if not (
            target.startswith("/usr/share/zoneinfo/")
            or target.startswith("../usr/share/zoneinfo/")
        ) or ".." in target.removeprefix("../").split("/"):
            raise Conflict("localtime has an unexpected symlink target")

    r = runner or Native()
    values = r.run(
        "timedatectl",
        "show",
        "--property=Timezone",
        "--property=LocalRTC",
    ).stdout
    state = dict(line.split("=", 1) for line in values.splitlines() if "=" in line)
    if state.get("LocalRTC") != "no":
        raise Conflict("local RTC requires an explicit operator decision")
    return state.get("Timezone")


def set_timezone(timezone: str, runner: Native | None = None) -> None:
    """Set system timezone using timedatectl."""
    r = runner or Native()
    r.run("timedatectl", "set-timezone", timezone)


def get_hostname(runner: Native | None = None, kind: str = "--static") -> str:
    """Get system hostname using hostnamectl."""
    r = runner or Native()
    return r.run("hostnamectl", kind).stdout.strip()


def set_hostname(
    hostname: str, runner: Native | None = None, kind: str = "--static"
) -> None:
    """Set system hostname using hostnamectl."""
    r = runner or Native()
    r.run("hostnamectl", kind, "set-hostname", hostname)


def is_ntp_synchronized(runner: Native | None = None) -> bool:
    """Check if network time synchronization is currently active."""
    r = runner or Native()
    output = r.run(
        "timedatectl", "show", "--property=NTPSynchronized", "--value"
    ).stdout.strip()
    return output == "yes"


def check_discard_support(runner: Native | None = None) -> bool:
    """Inspect mounted block devices with lsblk to verify SSD trim/discard support."""
    r = runner or Native()
    data = json.loads(
        r.run(
            "lsblk",
            "--json",
            "--bytes",
            "--output",
            "NAME,TYPE,DISC-MAX,MOUNTPOINTS",
        ).stdout
    )

    def walk(devices: list[dict], encrypted: bool = False) -> bool:
        suitable = False
        for device in devices:
            crypt = encrypted or device.get("type") == "crypt"
            mounted = any(device.get("mountpoints") or [])
            if mounted and crypt:
                raise Conflict(
                    "encrypted mounted storage needs a separate discard policy"
                )
            suitable |= mounted and int(device.get("disc-max") or 0) > 0
            suitable |= walk(device.get("children", []), crypt)
        return suitable

    devices = data.get("blockdevices", [])
    if not walk(devices):
        raise Conflict("no mounted discard-capable device was found")
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
    """Ensure a user has an allocated range in /etc/subuid or /etc/subgid without overlaps."""
    from .io import atomic_write, read_managed

    p = Path(path)
    lines = read_managed(p).splitlines() if p.exists() else []
    user_indices: list[int] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = stripped.split(":")
        if len(fields) != 3 or not fields[1].isdigit() or not fields[2].isdigit():
            raise Conflict(f"invalid subordinate ID allocation in {p}")
        existing_user, start_text, count_text = fields
        if existing_user == user:
            user_indices.append(index)
            continue
        existing = {"start": int(start_text), "count": int(count_text)}
        if ranges_overlap(desired, existing):
            raise Conflict(
                f"desired subordinate ID range for {user} overlaps {existing_user} in {p}"
            )

    if len(user_indices) > 1:
        raise Conflict(f"multiple subordinate ID allocations exist for {user} in {p}")
    desired_line = f"{user}:{desired['start']}:{desired['count']}"
    if user_indices:
        lines[user_indices[0]] = desired_line
    else:
        lines.append(desired_line)
    target_uid = os.geteuid() if uid is None else uid
    target_gid = os.getegid() if gid is None else gid
    atomic_write(p, "\n".join(lines) + "\n", mode=0o644, uid=target_uid, gid=target_gid)
