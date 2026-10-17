"""Safe file ownership, atomic replacement and configuration parsing."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shlex
import stat
import tempfile

from .exceptions import Conflict


def assignments(text: str) -> dict[str, str]:
    """Parse environment-style files without evaluating shell or losing comments."""
    result: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"\s*([A-Z][A-Z0-9_]*)\s*=\s*(.*?)\s*", line)
        if not match:
            raise Conflict("unsupported shared configuration syntax")
        key, raw = match.groups()
        try:
            words = shlex.split(raw, comments=True)
        except ValueError:
            raise Conflict("invalid shared configuration quoting") from None
        if len(words) > 1 or key in result or any(c in raw for c in ("$", "`", "\\")):
            raise Conflict("ambiguous shared configuration assignment")
        result[key] = words[0] if words else ""
    return result


def replace_keys(text: str, desired: dict[str, str]) -> str:
    assignments(text)  # Validate every line, including unowned assignments.
    remaining = dict(desired)
    lines: list[str] = []
    for line in text.splitlines(keepends=True):
        match = re.match(r"\s*([A-Z][A-Z0-9_]*)\s*=", line)
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


def locale_gen(text: str, locales: list[str]) -> str:
    begin, end = "# BEGIN nix-config locales", "# END nix-config locales"
    outside: list[str] = []
    inside, seen = False, False
    for line in text.splitlines(keepends=True):
        if line.strip() == begin:
            if inside or seen:
                raise Conflict("duplicate locale managed block")
            inside, seen = True, True
        elif line.strip() == end:
            if not inside:
                raise Conflict("unmatched locale managed block")
            inside = False
        elif not inside:
            entry = line.split("#", 1)[0].strip()
            if entry and not re.fullmatch(r"[A-Za-z0-9_.@-]+\s+[A-Za-z0-9-]+", entry):
                raise Conflict("unsupported locale generator syntax")
            outside.append(line)
    if inside:
        raise Conflict("unterminated locale managed block")
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


def ini(text: str) -> dict[tuple[str, str], str]:
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
                raise Conflict("duplicate systemd configuration key")
            result[pair] = val.strip()
        else:
            raise Conflict("unsupported systemd configuration syntax")
    return result


class Files:
    def __init__(
        self, root: Path | str = Path("/"), identity: tuple[int, int] = (0, 0)
    ):
        self.root = Path(root)
        self.identity = identity

    def system_name(self, path: Path) -> str:
        """The host path that a root-relative path stands for, for diagnostics."""
        return "/" + str(path.relative_to(self.root))

    def path(self, name: str, symlink_leaf: bool = False) -> Path:
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
        if self.matches(name, text, mode):
            return False
        path = self.path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".nix-config.", dir=path.parent)
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
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def zone_exists(self, name: str) -> bool:
        base = self.path("/usr/share/zoneinfo")
        candidate = base / name
        resolved = candidate.resolve()
        if not resolved.is_relative_to(base):
            raise Conflict("zoneinfo alias escapes the native database")
        return resolved.is_file()

    def metadata_matches(self, name: str, symlink: bool = False) -> bool:
        path = self.path(name, symlink_leaf=symlink)
        if not path.exists() and not path.is_symlink():
            return False
        info = path.lstat()
        return (info.st_uid, info.st_gid) == self.identity and (
            symlink or stat.S_IMODE(info.st_mode) == 0o644
        )

    def repair_metadata(self, name: str, symlink: bool = False) -> bool:
        path = self.path(name, symlink_leaf=symlink)
        if not self.metadata_matches(name, symlink):
            os.chown(path, *self.identity, follow_symlinks=False)
            if not symlink:
                os.chmod(path, 0o644, follow_symlinks=False)
            self.sync_dir(path.parent)
            return True
        return False

    def timer_dropin(self, content: str) -> tuple[str, str]:
        target = "/etc/systemd/system/fstrim.timer.d/60-nix-config.conf"
        self.read(target)
        for base in ("/usr/lib", "/usr/local/lib", "/run", "/etc"):
            directory = self.path(base + "/systemd/system/fstrim.timer.d")
            if directory.exists():
                for file in directory.glob("*.conf"):
                    name = "/" + str(file.relative_to(self.root))
                    if name != target:
                        values = ini(self.read(name))
                        if any(
                            section == "Timer" and key.startswith("On")
                            for section, key in values
                        ):
                            raise Conflict(
                                f"custom TRIM timing in {name} requires operator review"
                            )
                        if ("Timer", "Persistent") in values and values[
                            ("Timer", "Persistent")
                        ] != "false":
                            persistent_val = values[("Timer", "Persistent")]
                            raise Conflict(
                                f"conflicting TRIM timer persistence override in {name} (Persistent={persistent_val})"
                            )
        return target, content

    def marker(self, action: str) -> str:
        return f"/var/lib/nix-config/arch/system-{action}.pending"

    def pending(self, action: str) -> bool:
        name = self.marker(action)
        self.read(name)
        return self.path(name).exists()

    def mark(self, action: str, value: str = "pending\n") -> None:
        self.write(self.marker(action), value, 0o600)

    def clear(self, action: str) -> None:
        path = self.path(self.marker(action))
        if path.exists():
            path.unlink()
            self.sync_dir(path.parent)

    def dropin(self, name: str, text: str) -> str:
        target = f"/etc/systemd/{name}.conf.d/60-nix-config.conf"
        desired = ini(text)
        # Respect systemd's basename precedence across vendor/runtime/admin files.
        bases = ["/usr/lib", "/usr/local/lib", "/run", "/etc"]
        main = next(
            (
                f"{base}/systemd/{name}.conf"
                for base in reversed(bases)
                if self.path(f"{base}/systemd/{name}.conf").exists()
            ),
            None,
        )
        candidates: dict[str, str] = {}
        for base in bases:
            directory = self.path(f"{base}/systemd/{name}.conf.d")
            if directory.exists():
                for path in directory.glob("*.conf"):
                    candidates[path.name] = "/" + str(path.relative_to(self.root))
        sources = ([main] if main else []) + [
            candidates[key] for key in sorted(candidates)
        ]
        self.read(target)
        for source in sources:
            if source == target:
                continue
            path = self.path(source, symlink_leaf=True)
            if path.is_symlink() and os.readlink(path) == "/dev/null":
                continue
            values = ini(self.read(source))
            conflicts = [
                f"[{sec}] {k}={values[(sec, k)]} (expected: {v})"
                for (sec, k), v in desired.items()
                if (sec, k) in values and values[(sec, k)] != v
            ]
            if conflicts:
                details = ", ".join(conflicts)
                raise Conflict(
                    f"conflicting {name} configuration in {source} ({details}); review other overrides"
                )
        return target
