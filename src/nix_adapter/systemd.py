"""Systemd service and unit management."""

from __future__ import annotations
from typing import Sequence

from .exceptions import Conflict
from .native import Native


class Systemd:
    def __init__(self, runner: Native | None = None, user: bool = False):
        self.runner = runner or Native()
        self.user = user

    def _cmd(self, *args: str) -> list[str]:
        cmd = ["systemctl"]
        if self.user:
            cmd.append("--user")
        cmd.extend(args)
        return cmd

    def show(
        self, unit: str, properties: Sequence[str] | None = None
    ) -> dict[str, str]:
        args = ["show", unit]
        if properties:
            args.append(f"--property={','.join(properties)}")
        res = self.runner.run(*self._cmd(*args))
        return dict(
            line.split("=", 1) for line in res.stdout.splitlines() if "=" in line
        )

    def ready_unit(self, unit: str) -> dict[str, str]:
        state = self.show(
            unit, properties=["LoadState", "ActiveState", "UnitFileState"]
        )
        if state.get("LoadState") != "loaded" or state.get("UnitFileState") in (
            "masked",
            "masked-runtime",
        ):
            raise Conflict(f"required unit {unit} is missing or masked")
        return state

    def is_active(self, unit: str) -> bool:
        res = self.runner.run(*self._cmd("is-active", "--quiet", unit), check=False)
        return res.returncode == 0

    def is_enabled(self, unit: str) -> bool:
        res = self.runner.run(*self._cmd("is-enabled", "--quiet", unit), check=False)
        return res.returncode == 0

    def enable(self, unit: str, now: bool = False) -> None:
        args = ["enable"]
        if now:
            args.append("--now")
        args.append(unit)
        self.runner.run(*self._cmd(*args))

    def disable(self, unit: str, now: bool = False) -> None:
        args = ["disable"]
        if now:
            args.append("--now")
        args.append(unit)
        self.runner.run(*self._cmd(*args))

    def start(self, unit: str) -> None:
        self.runner.run(*self._cmd("start", unit))

    def stop(self, unit: str) -> None:
        self.runner.run(*self._cmd("stop", unit))

    def restart(self, unit: str) -> None:
        self.runner.run(*self._cmd("restart", unit))

    def reload(self, unit: str) -> None:
        self.runner.run(*self._cmd("reload", unit))

    def daemon_reload(self) -> None:
        self.runner.run(*self._cmd("daemon-reload"))
