"""Primitives for reconciling native host state against Nix-evaluated declarations."""

from .base import BaseAdapter
from .cli import run_adapter_cli
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
from .native import Native
from .plan import Change, render
from .progress import Progress, Task
from .service import BaseServiceAdapter
from .system import ensure_subordinate_range
from .systemd import Systemd
from .testing import FakeNative, FakeProcess
from .toml import dump_toml, parse_toml

__version__ = "0.5.0"

__all__ = [
    "BaseAdapter",
    "BaseServiceAdapter",
    "Change",
    "Conflict",
    "FakeNative",
    "FakeProcess",
    "Files",
    "Native",
    "NotReady",
    "Progress",
    "Systemd",
    "Task",
    "assignments",
    "atomic_write",
    "dump_toml",
    "ensure_directory",
    "ensure_subordinate_range",
    "ini",
    "locale_gen",
    "operation_lock",
    "parse_toml",
    "read_managed",
    "regular_file",
    "remove_managed_file",
    "render",
    "replace_keys",
    "run_adapter_cli",
]
