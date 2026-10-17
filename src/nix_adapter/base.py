"""Base adapter class defining standard lifecycle and resource contracts."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast

from .exceptions import Conflict
from .files import Files
from .native import Native
from .plan import Change
from .systemd import Systemd


class BaseAdapter[M: Mapping[str, Any] = dict[str, Any]]:
    """Base for adapters that converge native state toward a manifest of type M."""

    def __init__(
        self,
        desired: M,
        root: Path | str = Path("/"),
        runner: Native | None = None,
        files: Files | None = None,
    ):
        self.desired = desired
        self.root = Path(root)
        self.runner = runner or Native()
        self.files = files or Files(self.root)
        self.systemd = Systemd(self.runner)
        self.updates: int = 0
        self.actions: int = 0

    @property
    def native(self) -> Native:
        """Backwards-compatibility alias for self.runner."""
        return self.runner

    @native.setter
    def native(self, value: Native) -> None:
        self.runner = value
        self.systemd = Systemd(self.runner)

    def run(self, *args: Any, **kwargs: Any) -> Any:
        return self.runner.run(*args, **kwargs)

    def unit(self, name: str) -> dict[str, str]:
        """Inspect unit state properties."""
        return self.systemd.show(
            name, properties=["LoadState", "ActiveState", "UnitFileState"]
        )

    def ready_unit(self, name: str) -> dict[str, str]:
        """Verify that a unit is loaded and not masked."""
        state = self.unit(name)
        if state.get("LoadState") != "loaded" or state.get("UnitFileState") in (
            "masked",
            "masked-runtime",
        ):
            raise Conflict(f"required system unit is missing or masked: {name}")
        return state

    def converge_unit(
        self,
        name: str,
        *,
        restart: bool = False,
        before_change: Callable[[], None] | None = None,
    ) -> None:
        """Keep a loaded unit enabled and active; restart it when asked.

        `before_change` runs before each mutation, so callers can record a
        pending marker. The unit must end enabled and active, or this fails.
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
        """Compare a managed file with its declared content, ownership and mode."""
        if not self.files.path(name).exists():
            return Change("+", name)
        if self.files.read(name) != text:
            return Change("~", f"{name} (content differs)")
        if not self.files.matches(name, text):
            return Change("~", f"{name} (mode or owner differs)")
        return Change("=", name)

    def plan_unit(self, name: str) -> list[Change]:
        """Judge a unit as converge_unit does, so the plan matches the deployment."""
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
        """Resolve a declared path string, checking paths dictionary or direct keys."""
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
        """Resolve a declared path relative to root, checking paths dictionary or direct keys."""
        raw = self.path_str(key, default)
        return self.root / raw.lstrip("/")

    def write(
        self, path: str | Path, text: str, action: str, mode: int = 0o644
    ) -> bool:
        """Write managed configuration atomically and record updates and pending state."""
        p_str = str(path)
        if not self.files.matches(p_str, text, mode=mode):
            self.files.mark(action)
            changed = self.files.write(p_str, text, mode=mode)
            self.updates += int(changed)
            return changed
        return False

    def preflight(self, installed: bool = False) -> bool | None:
        """Validate prerequisites before convergence."""
        return None

    def converge(self) -> None:
        """Reconcile state to match desired configuration."""
        pass
