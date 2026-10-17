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

OPTIONAL_NOT_READY = 20


def is_verbose() -> bool:
    return len(sys.argv) == 4 and sys.argv[3] == "--verbose"


def run_adapter_cli(
    adapter_factory: Callable[[dict[str, Any]], Any],
    name: str = "Adapter",
    *,
    allow_skip: bool = False,
    require_root: bool = True,
) -> int:
    """Run an adapter lifecycle phase (preflight, converge or purge) from argv.

    purge is accepted only from adapters that implement it.

    Handles CLI arguments, verbose flag, root checks, and translates exceptions
    to standard exit codes.
    """
    if (
        len(sys.argv) not in (3, 4)
        or sys.argv[2] not in ("preflight", "converge", "purge")
        or (len(sys.argv) == 4 and sys.argv[3] != "--verbose")
        or (require_root and os.geteuid() != 0)
    ):
        raise Conflict(f"private {name} adapter must be invoked by arch-switch")

    if is_verbose():
        os.environ["NIX_CONFIG_VERBOSE"] = "1"

    manifest_path = Path(sys.argv[1])
    desired = json.loads(manifest_path.read_text())
    adapter = adapter_factory(desired)
    phase = sys.argv[2]
    if phase == "purge" and not hasattr(adapter, "purge"):
        raise Conflict(f"{name} adapter does not support purge")

    try:
        if phase == "preflight":
            result = adapter.preflight()
            if allow_skip and result is False:
                return OPTIONAL_NOT_READY
        elif phase == "purge":
            adapter.purge()
        else:
            if hasattr(adapter, "converge"):
                adapter.converge()
            elif hasattr(adapter, "preflight"):
                adapter.preflight()
        return 0
    except NotReady as error:
        print(f"{name} not ready: {error}", file=sys.stderr)
        return OPTIONAL_NOT_READY
    except Conflict as error:
        print(f"{name}: {error}", file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError) as error:
        print(f"{name} failed: {type(error).__name__}: {error}", file=sys.stderr)
        if is_verbose():
            traceback.print_exc()
        return 1
