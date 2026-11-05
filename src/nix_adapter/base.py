"""Base adapter class defining standard lifecycle and resource contracts."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, ClassVar, cast

from .context import AdapterContext
from .exceptions import Conflict
from .files import Files
from .native import Native
from .plan import Change
from .systemd import UNIT_STATE, Systemd


class BaseAdapter[M: Mapping[str, Any] = dict[str, Any]]:
    """Base for adapters that converge native state toward a manifest of type M.

    Subclasses that record pending markers set `pending_template`, a host path
    containing `{action}`.
    """

    pending_template: ClassVar[str | None] = None

    def __init__(
        self,
        desired: M,
        root: Path | str = Path("/"),
        native: Native | None = None,
        files: Files | None = None,
        context: AdapterContext | None = None,
    ):
        self.desired = desired
        self.root = Path(root)
        self.context = context or AdapterContext()
        self.files = self._files(files)
        self.native = native or self.context.native()
        self.updates: int = 0
        self.actions: int = 0

    def _files(self, files: Files | None) -> Files:
        """The adapter's files, given this adapter's pending-marker template."""
        template = self.pending_template
        if files is None:
            return Files(self.root, pending_template=template)
        if files.pending_template is None and template is not None:
            return files.with_pending_template(template)
        return files

    @property
    def native(self) -> Native:
        """The command runner; assigning one also rebuilds `systemd` on it."""
        return self._native

    @native.setter
    def native(self, value: Native) -> None:
        self._native = value
        self.systemd = Systemd(value)

    def run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        """Run a native command through this adapter's runner.

        Raise `Conflict` as `Native.run` does.
        """
        return self.native.run(*args, check=check)

    def unit(self, name: str) -> dict[str, str]:
        """The load, active and unit-file state of unit `name`.

        Raise `Conflict` if `systemctl show` fails.
        """
        return self.systemd.show(name, properties=UNIT_STATE)

    def ready_unit(self, name: str) -> dict[str, str]:
        """The state of unit `name`; raise `Conflict` if it is missing or masked."""
        return self.systemd.ready_unit(name)

    def converge_unit(
        self,
        name: str,
        *,
        restart: bool = False,
        before_change: Callable[[], None] | None = None,
    ) -> None:
        """Keep a loaded unit enabled and active; restart it when asked.

        `before_change` runs before each mutation, so callers can record a
        pending marker. Raise `Conflict` if the unit is missing or masked, or
        does not end enabled and active.
        """
        state = self.ready_unit(name)
        if state.get("UnitFileState") != "enabled":
            if before_change is not None:
                before_change()
            self.systemd.enable(name)
            self.actions += 1
        if state.get("ActiveState") != "active" or restart:
            if before_change is not None:
                before_change()
            if restart:
                self.systemd.restart(name)
            else:
                self.systemd.start(name)
            self.actions += 1
        state = self.ready_unit(name)
        if (
            state.get("ActiveState") != "active"
            or state.get("UnitFileState") != "enabled"
        ):
            raise Conflict(f"required unit {name} did not become enabled and active")

    def plan(self) -> list[Change]:
        """What converge would change; adapters override this."""
        return []

    def plan_file(self, name: str, text: str) -> Change:
        """Compare a managed file with its declared content, ownership and mode.

        Raise `Conflict` as `Files.read` does.
        """
        if not self.files.path(name).exists():
            return Change("+", name)
        if self.files.read(name) != text:
            return Change("~", f"{name} (content differs)")
        if not self.files.matches(name, text):
            return Change("~", f"{name} (mode or owner differs)")
        return Change("=", name)

    def plan_unit(self, name: str) -> list[Change]:
        """Judge a unit as `converge_unit` does, so the plan matches the deployment.

        Raise `Conflict` as `ready_unit` does.
        """
        state = self.ready_unit(name)
        enabled = state.get("UnitFileState") == "enabled"
        active = state.get("ActiveState") == "active"
        changes: list[Change] = []
        if not enabled:
            changes.append(Change("~", f"enable {name}"))
        if not active:
            changes.append(Change("~", f"start {name}"))
        return changes or [Change("=", name)]

    def path_str(self, key: str, default: str | None = None) -> str:
        """The host path that manifest key `key` declares.

        The key is looked up in the manifest's `paths` table if it has one,
        else at the top level; `default` applies when it is absent. Raise
        `Conflict` unless the value is an absolute path without `..`.
        """
        paths: object = self.desired.get("paths")
        if isinstance(paths, dict):
            value = cast("dict[str, object]", paths).get(key)
        else:
            value = self.desired.get(key)
        if value is None and default is not None:
            value = default
        if (
            not isinstance(value, str)
            or not value.startswith("/")
            or ".." in Path(value).parts
        ):
            raise Conflict(f"invalid manifest path for {key}")
        return value

    def path(self, key: str, default: str | None = None) -> Path:
        """`path_str(key, default)` as a path under `root`; raise as `path_str` does."""
        raw = self.path_str(key, default)
        return self.root / raw.lstrip("/")

    def write(
        self, path: str | Path, text: str, action: str, mode: int = 0o644
    ) -> bool:
        """Replace `path` with `text` if it differs; return whether it changed.

        The pending marker for `action` is recorded before the write, so the
        adapter needs a `pending_template`; without one this raises `Conflict`.
        """
        p_str = str(path)
        if not self.files.matches(p_str, text, mode=mode):
            self.files.mark(action)
            changed = self.files.write(p_str, text, mode=mode)
            self.updates += int(changed)
            return changed
        return False

    def preflight(self) -> bool | None:
        """Check prerequisites; adapters override this. `False` means not ready."""
        return None

    def converge(self) -> None:
        """Reconcile state to match desired configuration."""
        pass
