"""Native command adapter with lossless failure diagnostics."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from collections.abc import Iterable

from .exceptions import Conflict


class Native:
    """Run host commands while preserving complete diagnostics.

    Normal operation captures output because callers inspect stdout. On failure,
    both streams are attached to the raised Conflict. Verbose operation also
    prints each command and its complete captured streams.
    """

    def __init__(self, *, verbose: bool | None = None, timeout: int = 300):
        self.verbose = (
            os.environ.get("NIX_CONFIG_VERBOSE") == "1" if verbose is None else verbose
        )
        self.timeout = timeout

    def available(self, command: str) -> bool:
        return os.access("/usr/bin/" + command, os.X_OK)

    @staticmethod
    def _text(value: str | bytes | None) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode(errors="replace")
        return value

    @staticmethod
    def _section(name: str, value: str) -> str:
        return f"{name}:\n{value}" if value else f"{name}: <empty>"

    def _emit(self, stdout: str, stderr: str) -> None:
        if stdout:
            sys.stdout.write(stdout)
            if not stdout.endswith("\n"):
                sys.stdout.write("\n")
            sys.stdout.flush()
        if stderr:
            sys.stderr.write(stderr)
            if not stderr.endswith("\n"):
                sys.stderr.write("\n")
            sys.stderr.flush()

    def _failure(
        self,
        summary: str,
        argv: Iterable[str],
        stdout: str = "",
        stderr: str = "",
    ) -> Conflict:
        command = shlex.join(list(argv))
        details = [f"{summary}: {command}"]
        if not self.verbose:
            details.extend(
                [self._section("stdout", stdout), self._section("stderr", stderr)]
            )
        return Conflict("\n".join(details))

    def run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        if not args:
            raise ValueError("native command is required")
        argv = ["/usr/bin/" + args[0], *args[1:]]
        if self.verbose:
            print(f"+ {shlex.join(argv)}", file=sys.stderr, flush=True)
        try:
            result = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env={"PATH": "/usr/bin", "LC_ALL": "C"},
                cwd="/",
            )
        except subprocess.TimeoutExpired as error:
            stdout = self._text(error.stdout)
            stderr = self._text(error.stderr)
            if self.verbose:
                self._emit(stdout, stderr)
            raise self._failure(
                f"native command timed out after {self.timeout}s",
                argv,
                stdout,
                stderr,
            ) from None
        except OSError as error:
            raise Conflict(
                f"native command could not start: {shlex.join(argv)}: "
                f"{type(error).__name__}: {error}"
            ) from None

        if self.verbose:
            self._emit(result.stdout, result.stderr)
        if check and result.returncode:
            raise self._failure(
                f"native command failed with exit {result.returncode}",
                argv,
                result.stdout,
                result.stderr,
            )
        return result
