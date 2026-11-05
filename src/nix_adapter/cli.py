"""Command-line harness for privileged native adapters.

An adapter runs as `adapter MANIFEST PHASE [--verbose] [--color]`. The harness
validates the invocation, loads the manifest, builds the adapter and maps the
phase outcome to an exit status.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast, runtime_checkable

from .context import AdapterContext
from .exceptions import Conflict, NotReady
from .plan import Change, render

OPTIONAL_NOT_READY = 20
"""Exit status of an optional adapter whose preparation never completed."""

FLAGS = frozenset({"--verbose", "--color"})
USAGE = "MANIFEST PHASE [--verbose] [--color]"


@runtime_checkable
class Adapter(Protocol):
    """The phases that every adapter implements."""

    def preflight(self) -> bool | None:
        """Check prerequisites; `False` means an optional adapter is not ready."""
        ...

    def plan(self) -> list[Change]:
        """Report what `converge` would change."""
        ...

    def converge(self) -> None:
        """Reconcile host state with the manifest."""
        ...


@runtime_checkable
class Purgeable(Protocol):
    """An adapter that can withdraw the state it manages."""

    def purge(self) -> None:
        """Remove the managed state."""
        ...


@runtime_checkable
class ExtraPhases(Protocol):
    """An adapter with phases beyond the standard four, keyed by name."""

    @property
    def phases(self) -> Mapping[str, Callable[[], None]]:
        """Each extra phase name and the callable that runs it."""
        ...


type AdapterFactory = Callable[[Mapping[str, object], AdapterContext], Adapter]


@dataclass(frozen=True)
class Invocation:
    """A validated adapter command line."""

    manifest: Path
    phase: str
    context: AdapterContext


def parse_invocation(
    args: Sequence[str], name: str, *, require_root: bool
) -> Invocation:
    """Validate `MANIFEST PHASE [FLAGS]`.

    Raise `Conflict` on any other shape, or if `require_root` is set and the
    process is not root.
    """
    flags = list(args[2:])
    valid = (
        len(args) >= 2
        and args[1].isidentifier()
        and set(flags) <= FLAGS
        and len(set(flags)) == len(flags)
    )
    if not valid:
        raise Conflict(f"{name} adapter usage: {USAGE}")
    if require_root and os.geteuid() != 0:
        raise Conflict(f"{name} adapter must run as root")
    context = AdapterContext(verbose="--verbose" in flags, color="--color" in flags)
    return Invocation(Path(args[0]), args[1], context)


def load_manifest(path: Path, name: str) -> Mapping[str, object]:
    """Read a manifest file that holds one JSON object.

    Raise `Conflict` if its top level is not an object; JSON syntax errors
    raise `ValueError`.
    """
    manifest: object = json.loads(path.read_text())
    if not isinstance(manifest, dict):
        raise Conflict(f"{name} manifest {path} is not a JSON object")
    return cast("dict[str, object]", manifest)


def phase_runner(
    adapter: Adapter, invocation: Invocation, name: str, *, allow_skip: bool
) -> Callable[[], int]:
    """The callable that runs the invoked phase and returns its exit status.

    Raises `Conflict` before anything runs when the adapter lacks the phase.
    """
    color = invocation.context.color
    match invocation.phase:
        case "plan":
            return lambda: print_plan(adapter.plan(), name, color=color)
        case "preflight":
            return lambda: preflight_status(adapter.preflight(), allow_skip=allow_skip)
        case "converge":
            return exit_zero_after(adapter.converge)
        case "purge" if isinstance(adapter, Purgeable):
            return exit_zero_after(adapter.purge)
        case phase if isinstance(adapter, ExtraPhases) and phase in adapter.phases:
            return exit_zero_after(adapter.phases[phase])
        case phase:
            raise Conflict(f"{name} adapter does not support {phase}")


def exit_zero_after(action: Callable[[], None]) -> Callable[[], int]:
    """Wrap a phase that reports failure only by raising."""

    def run() -> int:
        action()
        return 0

    return run


def print_plan(changes: list[Change], name: str, *, color: bool) -> int:
    """Print the plan; fail when it holds a conflict."""
    print(render(changes, name, color=color))
    return 1 if any(change.kind == "!" for change in changes) else 0


def preflight_status(ready: bool | None, *, allow_skip: bool) -> int:
    """Exit status of a preflight that returned `ready`."""
    return OPTIONAL_NOT_READY if allow_skip and ready is False else 0


def run_adapter_cli(
    factory: AdapterFactory,
    name: str = "Adapter",
    *,
    allow_skip: bool = False,
    require_root: bool = True,
    argv: Sequence[str] | None = None,
) -> int:
    """Run one adapter phase from the command line and return its exit status.

    `plan` prints the adapter's changes and fails when one is a conflict.
    `preflight` returning `False` exits with `OPTIONAL_NOT_READY` when
    `allow_skip` is set; `NotReady` always does.
    """
    invocation = parse_invocation(
        sys.argv[1:] if argv is None else argv, name, require_root=require_root
    )
    adapter = factory(load_manifest(invocation.manifest, name), invocation.context)
    run_phase = phase_runner(adapter, invocation, name, allow_skip=allow_skip)
    try:
        return run_phase()
    except NotReady as error:
        print(f"{name} not ready: {error}", file=sys.stderr)
        return OPTIONAL_NOT_READY
    except Conflict as error:
        report_conflict(error, invocation, name)
        return 1
    except (OSError, ValueError, KeyError) as error:
        print(f"{name} failed: {type(error).__name__}: {error}", file=sys.stderr)
        if invocation.context.verbose:
            traceback.print_exc()
        return 1


def report_conflict(error: Conflict, invocation: Invocation, name: str) -> None:
    """Print a conflict as a plan line during `plan`, else as an error."""
    if invocation.phase == "plan":
        line = render([Change("!", str(error))], name, color=invocation.context.color)
        print(line)
    else:
        print(f"{name}: {error}", file=sys.stderr)
