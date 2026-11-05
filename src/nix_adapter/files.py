"""Safe file ownership, atomic replacement and configuration parsing."""

from __future__ import annotations

import os
import re
import shlex
import stat
import tempfile
from pathlib import Path

from .exceptions import Conflict

KEY = r"[A-Z][A-Z0-9_]*"
ASSIGNMENT = re.compile(rf"\s*({KEY})\s*=\s*(.*?)\s*")
SHELL_EXPANSION = ("$", "`", "\\")


def assignments(text: str, *, source: str) -> dict[str, str]:
    """Parse an environment-style file without evaluating shell syntax.

    Each line is blank, a comment or one `KEY=value` with a single word value.
    Errors name `source`, the line and the key, never the value, which can
    hold a secret.
    """
    result: dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        location = f"{source}:{number}"
        match = ASSIGNMENT.fullmatch(line)
        if not match:
            raise Conflict(f"unsupported shared configuration syntax at {location}")
        key, raw = match.groups()
        try:
            words = shlex.split(raw, comments=True)
        except ValueError:
            raise Conflict(f"invalid quoting of {key} at {location}") from None
        if len(words) > 1 or key in result or any(c in raw for c in SHELL_EXPANSION):
            raise Conflict(f"ambiguous assignment of {key} at {location}")
        result[key] = words[0] if words else ""
    return result


def replace_keys(text: str, desired: dict[str, str], *, source: str) -> str:
    """Set the `desired` keys of an environment-style file and keep all else.

    Existing assignments are replaced in place; missing keys are appended.
    Raise `Conflict` as `assignments` does if any line is invalid.
    """
    assignments(text, source=source)  # Validate unowned lines too.
    remaining = dict(desired)
    lines: list[str] = []
    for line in text.splitlines(keepends=True):
        match = re.match(rf"\s*({KEY})\s*=", line)
        if match and match[1] in desired:
            key = match[1]
            lines.append(f"{key}={shlex.quote(remaining.pop(key))}\n")
        else:
            lines.append(line)
    result = "".join(lines)
    if remaining:
        if result and not result.endswith("\n"):
            result += "\n"
        result += "".join(
            f"{key}={shlex.quote(value)}\n" for key, value in remaining.items()
        )
    return result


def locale_gen(text: str, locales: list[str], *, block: str, source: str) -> str:
    """Add locales to a locale.gen text inside the managed block named `block`.

    The block is delimited by `# BEGIN <block>` and `# END <block>`; entries
    outside it are validated and kept as the operator wrote them. Raise
    `Conflict` on a duplicate, unmatched or unterminated block, or an entry
    that is not `<locale> <charset>`.
    """
    begin, end = f"# BEGIN {block}", f"# END {block}"
    outside: list[str] = []
    inside, seen = False, False
    for line in text.splitlines(keepends=True):
        if line.strip() == begin:
            if inside or seen:
                raise Conflict(f"duplicate managed block {block} in {source}")
            inside, seen = True, True
        elif line.strip() == end:
            if not inside:
                raise Conflict(f"unmatched end of managed block {block} in {source}")
            inside = False
        elif not inside:
            entry = line.split("#", 1)[0].strip()
            if entry and not re.fullmatch(r"[A-Za-z0-9_.@-]+\s+[A-Za-z0-9-]+", entry):
                raise Conflict(
                    f"unsupported locale generator syntax in {source}: {entry}"
                )
            outside.append(line)
    if inside:
        raise Conflict(f"unterminated managed block {block} in {source}")
    prefix = "".join(outside)
    enabled = {line.split("#", 1)[0].strip() for line in prefix.splitlines()}
    additions = [
        f"{locale} UTF-8" for locale in locales if f"{locale} UTF-8" not in enabled
    ]
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    return (
        prefix + begin + "\n" + "".join(line + "\n" for line in additions) + end + "\n"
    )


def ini(text: str, *, source: str) -> dict[tuple[str, str], str]:
    """Parse a systemd-style INI file into `(section, key)` pairs.

    Raise `Conflict` on a duplicate key, a key outside a section, or a
    continuation line.
    """
    section: str | None = None
    result: dict[tuple[str, str], str] = {}
    for line in text.splitlines():
        value = line.strip()
        if not value or value.startswith(("#", ";")):
            continue
        if value.startswith("[") and value.endswith("]"):
            section = value[1:-1]
        elif section and "=" in value and not value.endswith("\\"):
            key, val = value.split("=", 1)
            pair = (section, key.strip())
            if pair in result:
                raise Conflict(f"duplicate key [{section}] {pair[1]} in {source}")
            result[pair] = val.strip()
        else:
            raise Conflict(
                f"unsupported systemd configuration syntax in {source}: {value}"
            )
    return result


TEMPORARY_PREFIX = ".nix-adapter."
SYSTEMD_BASES = ("/usr/lib", "/usr/local/lib", "/run", "/etc")
"""Where systemd reads configuration, from vendor (lowest) to admin (highest)."""


class Files:
    """Owned files under `root`, written atomically with a fixed identity.

    `pending_template` names pending-marker files: a host path that contains
    `{action}`. Marker operations fail while no template is set.
    """

    def __init__(
        self,
        root: Path | str = Path("/"),
        identity: tuple[int, int] = (0, 0),
        *,
        pending_template: str | None = None,
    ):
        self.root = Path(root)
        self.identity = identity
        if pending_template is not None and "{action}" not in pending_template:
            raise Conflict(
                f"pending marker template lacks {{action}}: {pending_template}"
            )
        self.pending_template = pending_template

    def with_pending_template(self, template: str) -> Files:
        """A copy of these files that uses `template` for pending markers.

        Raise `Conflict` if `template` lacks `{action}`.
        """
        return type(self)(self.root, self.identity, pending_template=template)

    def system_name(self, path: Path) -> str:
        """The host path that a root-relative path stands for, for diagnostics."""
        return "/" + str(path.relative_to(self.root))

    def path(self, name: str, symlink_leaf: bool = False) -> Path:
        """The checked path under `root` for host path `name`.

        Raise `Conflict` on traversal, a symlink in the path (the leaf only if
        `symlink_leaf`), or an unsafe ancestor: not a directory, not owned by this
        identity, or writable by group or others.
        """
        relative = Path(name)
        if not relative.is_absolute() or ".." in relative.parts:
            raise Conflict(f"invalid private system path: {name}")
        path = self.root / str(relative).lstrip("/")
        for parent in reversed([path, *path.parents]):
            if parent == self.root or not parent.is_relative_to(self.root):
                continue
            if parent.is_symlink() and not (parent == path and symlink_leaf):
                raise Conflict(
                    f"symlink in managed system path: {self.system_name(parent)}"
                )
            if parent != path and parent.exists():
                info = parent.stat()
                if not stat.S_ISDIR(info.st_mode):
                    raise Conflict(
                        f"non-directory in managed system path: {self.system_name(parent)}"
                    )
                if info.st_uid != self.identity[0] or info.st_mode & 0o022:
                    raise Conflict(
                        "unsafe ownership or permissions in system path: "
                        f"{self.system_name(parent)} (owner {info.st_uid}, "
                        f"mode {stat.S_IMODE(info.st_mode):04o})"
                    )
        return path

    def read(self, name: str) -> str:
        """The text of `name`, or an empty string if it does not exist.

        Raise `Conflict` if the path is unsafe (see `path`), not a regular
        file, or not valid text.
        """
        path = self.path(name)
        if not path.exists():
            return ""
        if not path.is_file():
            raise Conflict(f"managed configuration is not a regular file: {name}")
        try:
            return path.read_text()
        except UnicodeError:
            raise Conflict(f"configuration is not valid text: {name}") from None

    def matches(self, name: str, text: str, mode: int = 0o644) -> bool:
        """Whether `name` holds `text` with `mode` and this identity.

        Raise `Conflict` as `read` does.
        """
        old = self.read(name)
        path = self.path(name)
        if not path.exists():
            return False
        info = path.stat()
        return (
            old == text
            and stat.S_IMODE(info.st_mode) == mode
            and (info.st_uid, info.st_gid) == self.identity
        )

    def write(self, name: str, text: str, mode: int = 0o644) -> bool:
        """Replace `name` atomically with `text`; return whether it changed.

        Raise `Conflict` as `read` does.
        """
        if self.matches(name, text, mode):
            return False
        path = self.path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=TEMPORARY_PREFIX, dir=path.parent
        )
        try:
            with os.fdopen(descriptor, "w") as stream:
                os.fchmod(stream.fileno(), mode)
                os.fchown(stream.fileno(), *self.identity)
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            self.sync_dir(path.parent)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return True

    @staticmethod
    def sync_dir(path: Path) -> None:
        """Flush the directory entry changes of `path` to disk."""
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def zone_exists(self, name: str) -> bool:
        """Whether `name` is a timezone in the host zoneinfo database.

        Raise `Conflict` if `name` resolves outside that database.
        """
        base = self.path("/usr/share/zoneinfo")
        candidate = base / name
        resolved = candidate.resolve()
        if not resolved.is_relative_to(base):
            raise Conflict(f"zoneinfo alias {name} escapes the native database")
        return resolved.is_file()

    def metadata_matches(self, name: str, symlink: bool = False) -> bool:
        """Whether `name` has this identity (and mode 0644 unless `symlink`).

        Raise `Conflict` as `path` does.
        """
        path = self.path(name, symlink_leaf=symlink)
        if not path.exists() and not path.is_symlink():
            return False
        info = path.lstat()
        return (info.st_uid, info.st_gid) == self.identity and (
            symlink or stat.S_IMODE(info.st_mode) == 0o644
        )

    def repair_metadata(self, name: str, symlink: bool = False) -> bool:
        """Restore the identity and mode of `name`; return whether it changed.

        Raise `Conflict` as `path` does.
        """
        path = self.path(name, symlink_leaf=symlink)
        if not self.metadata_matches(name, symlink):
            os.chown(path, *self.identity, follow_symlinks=False)
            if not symlink:
                os.chmod(path, 0o644, follow_symlinks=False)
            self.sync_dir(path.parent)
            return True
        return False

    def marker(self, action: str) -> str:
        """The host path of the pending marker for `action`.

        Raise `Conflict` if these files have no `pending_template`. The other
        marker methods raise the same way, and also as `path` does.
        """
        if self.pending_template is None:
            raise Conflict(f"no pending marker template for action {action}")
        return self.pending_template.format(action=action)

    def pending(self, action: str) -> bool:
        """Whether the pending marker for `action` exists."""
        name = self.marker(action)
        self.read(name)
        return self.path(name).exists()

    def mark(self, action: str, value: str = "pending\n") -> None:
        """Record that `action` has started but not finished."""
        self.write(self.marker(action), value, 0o600)

    def clear(self, action: str) -> None:
        """Remove the pending marker for `action`."""
        path = self.path(self.marker(action))
        if path.exists():
            path.unlink()
            self.sync_dir(path.parent)

    def dropin(self, name: str, text: str, *, file_name: str) -> str:
        """Check that `text` can own `/etc/systemd/<name>.conf.d/<file_name>`.

        Every other configuration source that systemd reads for `name` must
        agree with the declared values; the target path is returned. Raise
        `Conflict` on a disagreeing source, naming it and each value; on a
        source that `ini` rejects; or on an unsafe target path.
        """
        target = f"/etc/systemd/{name}.conf.d/{file_name}"
        desired = ini(text, source=f"declared {name} configuration")
        self.read(target)  # Validate the target path before trusting others.
        for source in self.systemd_sources(name):
            if source != target and not self._masked(source):
                self._check_agreement(name, source, desired)
        return target

    def systemd_sources(self, name: str) -> list[str]:
        """The host paths systemd reads for `<name>.conf`, lowest priority first.

        The main file comes from the highest-priority base that has one. Then
        come the drop-ins, ordered by basename; a basename in a later base
        replaces the same basename in an earlier one. Raise `Conflict` as
        `path` does for any of these directories.
        """
        main = next(
            (
                f"{base}/systemd/{name}.conf"
                for base in reversed(SYSTEMD_BASES)
                if self.path(f"{base}/systemd/{name}.conf").exists()
            ),
            None,
        )
        dropins: dict[str, str] = {}
        for base in SYSTEMD_BASES:
            directory = self.path(f"{base}/systemd/{name}.conf.d")
            if directory.exists():
                for path in directory.glob("*.conf"):
                    dropins[path.name] = self.system_name(path)
        return ([main] if main else []) + [dropins[key] for key in sorted(dropins)]

    def _masked(self, source: str) -> bool:
        path = self.path(source, symlink_leaf=True)
        return path.is_symlink() and os.readlink(path) == "/dev/null"

    def _check_agreement(
        self, name: str, source: str, desired: dict[tuple[str, str], str]
    ) -> None:
        values = ini(self.read(source), source=source)
        conflicts = [
            f"[{section}] {key}={values[(section, key)]} (expected: {value})"
            for (section, key), value in desired.items()
            if values.get((section, key), value) != value
        ]
        if conflicts:
            details = ", ".join(conflicts)
            raise Conflict(
                f"conflicting {name} configuration in {source} ({details}); "
                "review other overrides"
            )
