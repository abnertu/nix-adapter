"""Native command runner with lossless, redacted failure diagnostics."""

from __future__ import annotations

import copy
import os
import shlex
import subprocess
import sys
from collections.abc import Iterable, Sequence
from typing import Self, TextIO

from .exceptions import Conflict

COMMAND_DIRECTORY = "/usr/bin/"
COMMAND_ENVIRONMENT = {"PATH": "/usr/bin", "LC_ALL": "C"}
REDACTED = "<redacted>"


class Native:
    """Run host commands while preserving complete diagnostics.

    Normal operation captures output because callers inspect stdout. On
    failure, both streams are attached to the raised `Conflict`. Verbose
    operation also prints each command and its complete captured streams.

    Every diagnostic, printed or raised, replaces each `redact` value with
    `<redacted>`. The captured result that `run` returns is not redacted.
    """

    def __init__(
        self,
        *,
        verbose: bool = False,
        timeout: int = 300,
        redact: Sequence[str] = (),
    ):
        if any(not value for value in redact):
            raise ValueError("redacted values must be non-empty")
        self.verbose = verbose
        self.timeout = timeout
        self._secrets = _longest_first(redact)

    def redacting(self, *values: str) -> Self:
        """A copy that also redacts `values`; empty values are skipped.

        The copy keeps this runner's type and settings. An adapter assigns it
        through `BaseAdapter.native`, so its `Systemd` uses the copy too.
        """
        runner = copy.copy(self)
        runner._secrets = _longest_first([*self._secrets, *filter(None, values)])
        return runner

    def redact(self, text: str) -> str:
        """`text` with every configured secret replaced."""
        for secret in self._secrets:
            text = text.replace(secret, REDACTED)
        return text

    def available(self, command: str) -> bool:
        return os.access(COMMAND_DIRECTORY + command, os.X_OK)

    def run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        """Run `/usr/bin/<args[0]>` with a fixed environment from `/`.

        Raises `Conflict` when the command cannot start, times out, or exits
        nonzero while `check` is set.
        """
        if not args:
            raise ValueError("native command is required")
        argv = [COMMAND_DIRECTORY + args[0], *args[1:]]
        command = self._command_line(argv)
        if self.verbose:
            self._print(sys.stderr, f"+ {command}")
        try:
            result = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=COMMAND_ENVIRONMENT,
                cwd="/",
            )
        except subprocess.TimeoutExpired as error:
            stdout, stderr = _text(error.stdout), _text(error.stderr)
            if self.verbose:
                self._emit(stdout, stderr)
            summary = f"native command timed out after {self.timeout}s"
            raise self._failure(summary, command, stdout, stderr) from None
        except OSError as error:
            reason = f"{type(error).__name__}: {error}"
            message = f"native command could not start: {command}: {reason}"
            raise Conflict(self.redact(message)) from None

        if self.verbose:
            self._emit(result.stdout, result.stderr)
        if check and result.returncode:
            summary = f"native command failed with exit {result.returncode}"
            raise self._failure(summary, command, result.stdout, result.stderr)
        return result

    def _command_line(self, argv: Iterable[str]) -> str:
        # Redact each argument before quoting: quoting can rewrite a secret.
        return shlex.join(self.redact(argument) for argument in argv)

    def _print(self, stream: TextIO, text: str) -> None:
        stream.write(self.redact(text))
        if not text.endswith("\n"):
            stream.write("\n")
        stream.flush()

    def _emit(self, stdout: str, stderr: str) -> None:
        if stdout:
            self._print(sys.stdout, stdout)
        if stderr:
            self._print(sys.stderr, stderr)

    def _failure(
        self, summary: str, command: str, stdout: str, stderr: str
    ) -> Conflict:
        details = [f"{summary}: {command}"]
        # Verbose operation has already printed both streams.
        if not self.verbose:
            details += [_section("stdout", stdout), _section("stderr", stderr)]
        return Conflict(self.redact("\n".join(details)))


def _longest_first(values: Iterable[str]) -> tuple[str, ...]:
    # A secret that contains another must be replaced before it.
    return tuple(sorted(set(values), key=len, reverse=True))


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


def _section(name: str, value: str) -> str:
    return f"{name}:\n{value}" if value else f"{name}: <empty>"
