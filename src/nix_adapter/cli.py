"""CLI adapter harness for privileged native adapters."""

from __future__ import annotations

import json
import os
import sys
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .exceptions import Conflict, NotReady
from .plan import Change, render

OPTIONAL_NOT_READY = 20


FLAGS = frozenset({"--verbose", "--color"})
PHASES = ("preflight", "plan", "converge", "purge")


def is_verbose() -> bool:
    return "--verbose" in sys.argv[3:]


def run_adapter_cli(
    adapter_factory: Callable[[dict[str, Any]], Any],
    name: str = "Adapter",
    *,
    allow_skip: bool = False,
    require_root: bool = True,
    phases: tuple[str, ...] = (),
) -> int:
    """Run one adapter phase from argv: MANIFEST PHASE [--verbose] [--color].

    The phases are preflight, plan, converge and purge, plus the adapter's own
    `phases`; purge and extra phases run only on adapters that implement them.
    plan prints the adapter's changes and fails when one is a conflict.
    """
    args = sys.argv[1:]
    flags = set(args[2:])
    if (
        len(args) < 2
        or args[1] not in (*PHASES, *phases)
        or not flags <= FLAGS
        or len(flags) != len(args) - 2
        or (require_root and os.geteuid() != 0)
    ):
        raise Conflict(f"private {name} adapter must be invoked by arch-switch")

    if is_verbose():
        os.environ["NIX_CONFIG_VERBOSE"] = "1"

    desired = json.loads(Path(args[0]).read_text())
    adapter = adapter_factory(desired)
    phase = args[1]
    if phase not in ("preflight", "plan", "converge") and not hasattr(adapter, phase):
        raise Conflict(f"{name} adapter does not support {phase}")

    try:
        if phase == "preflight":
            result = adapter.preflight()
            if allow_skip and result is False:
                return OPTIONAL_NOT_READY
        elif phase == "plan":
            changes: list[Change] = adapter.plan()
            print(render(changes, name, color="--color" in flags))
            return 1 if any(change.kind == "!" for change in changes) else 0
        elif phase == "converge":
            if hasattr(adapter, "converge"):
                adapter.converge()
            else:
                adapter.preflight()
        else:
            getattr(adapter, phase)()
        return 0
    except NotReady as error:
        print(f"{name} not ready: {error}", file=sys.stderr)
        return OPTIONAL_NOT_READY
    except Conflict as error:
        if phase == "plan":
            print(render([Change("!", str(error))], name, color="--color" in flags))
        else:
            print(f"{name}: {error}", file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError) as error:
        print(f"{name} failed: {type(error).__name__}: {error}", file=sys.stderr)
        if is_verbose():
            traceback.print_exc()
        return 1
