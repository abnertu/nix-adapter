"""Systemd unit inspection and control through `systemctl`."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence

from .exceptions import Conflict
from .native import Native

UNIT_STATE = ("LoadState", "ActiveState", "UnitFileState")
MASKED = ("masked", "masked-runtime")


def key_values(output: str) -> dict[str, str]:
    """The `key=value` lines that `systemctl show` and `timedatectl show` print."""
    return dict(line.split("=", 1) for line in output.splitlines() if "=" in line)


class Systemd:
    """`systemctl` for the system manager, or for the user manager if `user`.

    Every method runs `systemctl` through `Native.run` and raises `Conflict`
    as it does: when the command cannot start or times out, and, except for
    the `is_*` queries, when it exits nonzero.
    """

    def __init__(self, native: Native, user: bool = False):
        self.native = native
        self.user = user

    def _systemctl(
        self, *args: str, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        scope = ["--user"] if self.user else []
        return self.native.run("systemctl", *scope, *args, check=check)

    def _succeeds(self, *args: str) -> bool:
        return self._systemctl(*args, check=False).returncode == 0

    def show(
        self, unit: str, properties: Sequence[str] | None = None
    ) -> dict[str, str]:
        """The unit's properties as reported by `systemctl show`.

        Raise `Conflict` if `systemctl` fails.
        """
        args = ["show", unit]
        if properties:
            args.append(f"--property={','.join(properties)}")
        return key_values(self._systemctl(*args).stdout)

    def ready_unit(self, unit: str) -> dict[str, str]:
        """The unit's state; raise `Conflict` if it is not loaded or is masked."""
        state = self.show(unit, properties=UNIT_STATE)
        if state.get("LoadState") != "loaded" or state.get("UnitFileState") in MASKED:
            raise Conflict(f"required unit {unit} is missing or masked")
        return state

    def is_active(self, unit: str) -> bool:
        """Whether the unit is active."""
        return self._succeeds("is-active", "--quiet", unit)

    def is_enabled(self, unit: str) -> bool:
        """Whether the unit is enabled."""
        return self._succeeds("is-enabled", "--quiet", unit)

    def enable(self, unit: str, now: bool = False) -> None:
        """Enable the unit, and start it too if `now`."""
        self._systemctl("enable", *(["--now"] if now else []), unit)

    def disable(self, unit: str, now: bool = False) -> None:
        """Disable the unit, and stop it too if `now`."""
        self._systemctl("disable", *(["--now"] if now else []), unit)

    def start(self, unit: str) -> None:
        """Start the unit."""
        self._systemctl("start", unit)

    def stop(self, unit: str) -> None:
        """Stop the unit."""
        self._systemctl("stop", unit)

    def restart(self, unit: str) -> None:
        """Restart the unit."""
        self._systemctl("restart", unit)

    def reload(self, unit: str) -> None:
        """Reload the unit's configuration."""
        self._systemctl("reload", unit)

    def daemon_reload(self) -> None:
        """Reload the manager's unit files."""
        self._systemctl("daemon-reload")
