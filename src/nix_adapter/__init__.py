"""Primitives for reconciling native host state against Nix-evaluated declarations."""

from importlib.metadata import version

from .base import BaseAdapter
from .cli import Adapter, ExtraPhases, Purgeable, run_adapter_cli
from .context import AdapterContext
from .exceptions import Conflict, NotReady
from .files import Files, assignments, ini, locale_gen, replace_keys
from .io import (
    atomic_write,
    ensure_directory,
    read_managed,
    regular_file,
    remove_managed_file,
)
from .lock import operation_lock
from .manifest import validate
from .native import Native
from .plan import STYLES, Change, render
from .progress import Progress, Task
from .service import BaseServiceAdapter
from .system import ensure_subordinate_range, inspect_subordinate_range, ranges_overlap
from .systemd import Systemd
from .testing import FakeNative, FakeProcess
from .toml import dump_toml, parse_toml

__version__ = version("nix_adapter")

__all__ = [
    "STYLES",
    "Adapter",
    "AdapterContext",
    "BaseAdapter",
    "BaseServiceAdapter",
    "Change",
    "Conflict",
    "ExtraPhases",
    "FakeNative",
    "FakeProcess",
    "Files",
    "Native",
    "NotReady",
    "Progress",
    "Purgeable",
    "Systemd",
    "Task",
    "assignments",
    "atomic_write",
    "dump_toml",
    "ensure_directory",
    "ensure_subordinate_range",
    "ini",
    "inspect_subordinate_range",
    "locale_gen",
    "operation_lock",
    "parse_toml",
    "ranges_overlap",
    "read_managed",
    "regular_file",
    "remove_managed_file",
    "render",
    "replace_keys",
    "run_adapter_cli",
    "validate",
]
