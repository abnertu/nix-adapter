"""Invocation settings that the CLI harness hands to each adapter."""

from __future__ import annotations

from dataclasses import dataclass

from .native import DEFAULT_TIMEOUT, Native


@dataclass(frozen=True)
class AdapterContext:
    """How the operator invoked an adapter.

    `verbose` echoes every native command and its output. `color` lets plan
    output use terminal colors.
    """

    verbose: bool = False
    color: bool = False

    def native(self, *, timeout: int = DEFAULT_TIMEOUT) -> Native:
        """A command runner that honors this invocation's verbosity."""
        return Native(verbose=self.verbose, timeout=timeout)
