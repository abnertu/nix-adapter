"""Test utilities and mock classes for adapters."""

from __future__ import annotations

import subprocess

from .native import COMMAND_DIRECTORY, Native


class FakeProcess(subprocess.CompletedProcess[str]):
    """Mock CompletedProcess return value."""

    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0):
        super().__init__(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class FakeNative(Native):
    """Mock runner for Native commands in adapter tests."""

    def __init__(
        self,
        available_commands: set[str] | list[str] | None = None,
        responses: dict[tuple[str, ...], FakeProcess | str] | None = None,
    ):
        super().__init__()
        self.calls: list[tuple[str, ...]] = []
        self.available_commands = set(available_commands or [])
        self.responses: dict[tuple[str, ...], FakeProcess] = {}
        if responses:
            for k, v in responses.items():
                if isinstance(v, str):
                    self.responses[k] = FakeProcess(stdout=v)
                else:
                    self.responses[k] = v

    def available(self, command: str) -> bool:
        """Whether `command` is in `available_commands` (all are when it is empty)."""
        if not self.available_commands:
            return True
        return command in self.available_commands

    def run(self, *argv: str, check: bool = True) -> FakeProcess:
        """Record `argv` and return its configured response.

        Raise `Conflict` on checked failures, as `Native.run` does.
        """
        if not argv:
            raise ValueError("native command is required")
        self.calls.append(argv)
        cmd = tuple(argv)
        res = self.responses.get(cmd, FakeProcess())
        if check and res.returncode != 0:
            if self.verbose:
                self._emit(res.stdout, res.stderr)
            raise self._failure(
                f"native command failed with exit {res.returncode}",
                self._command_line([COMMAND_DIRECTORY + argv[0], *argv[1:]]),
                res.stdout,
                res.stderr,
            )
        return res
