"""Test utilities and mock classes for adapters."""

from __future__ import annotations

import subprocess
from typing import Any

from .native import Native


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
        if not self.available_commands:
            return True
        return command in self.available_commands

    def run(
        self,
        *argv: str,
        check: bool = True,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
        allow_failure: bool = False,
        **kwargs: Any,
    ) -> FakeProcess:
        self.calls.append(argv)
        cmd = tuple(argv)
        res = self.responses.get(cmd, FakeProcess())
        if check and not allow_failure and res.returncode != 0:
            raise subprocess.CalledProcessError(
                res.returncode, list(argv), res.stdout, res.stderr
            )
        return res
