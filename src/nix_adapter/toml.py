"""TOML serialization and parsing utilities using standard library tomllib."""

from __future__ import annotations

import re
import tomllib
from typing import Any, cast


def _format_key(key: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]+", key):
        return key
    escaped = key.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


STRING_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}


def _dump_value(val: Any) -> str:
    match val:
        case bool():
            return "true" if val else "false"
        case int():
            return str(val)
        case float():
            text = str(val)
            return text if ("." in text or "e" in text.lower()) else f"{text}.0"
        case str():
            return '"' + "".join(STRING_ESCAPES.get(char, char) for char in val) + '"'
        case list():
            items = [_dump_value(item) for item in cast("list[Any]", val)]
            return f"[{', '.join(items)}]"
        case dict():
            pairs = cast("dict[str, Any]", val).items()
            items = [
                f"{_format_key(key)} = {_dump_value(value)}" for key, value in pairs
            ]
            return f"{{{', '.join(items)}}}"
        case _:
            raise TypeError(f"Unsupported TOML value type: {type(val)}")


def _is_table_array(val: Any) -> bool:
    items = cast("list[Any]", val) if isinstance(val, list) else []
    return bool(items) and all(isinstance(item, dict) for item in items)


def _header(path: list[str], *, array: bool) -> str:
    dotted = ".".join(_format_key(part) for part in path)
    return f"[[{dotted}]]" if array else f"[{dotted}]"


def _open_section(lines: list[str], header: str) -> None:
    if lines and lines[-1] != "":
        lines.append("")
    lines.append(header)


def _dump_table(data: dict[str, Any], path: list[str]) -> list[str]:
    """Plain keys first, then sub-tables, then arrays of tables."""
    lines = [
        f"{_format_key(key)} = {_dump_value(value)}"
        for key, value in data.items()
        if not isinstance(value, dict) and not _is_table_array(value)
    ]
    for key, value in data.items():
        if isinstance(value, dict):
            _open_section(lines, _header([*path, key], array=False))
            lines += _dump_table(cast("dict[str, Any]", value), [*path, key])
    for key, value in data.items():
        if _is_table_array(value):
            for item in cast("list[dict[str, Any]]", value):
                _open_section(lines, _header([*path, key], array=True))
                lines += _dump_table(item, [*path, key])
    return lines


def dump_toml(data: dict[str, Any]) -> str:
    """Serialize a dictionary to a TOML formatted string."""
    lines = _dump_table(data, [])
    res = "\n".join(lines)
    return res + "\n" if res and not res.endswith("\n") else res


def parse_toml(content: str | bytes) -> dict[str, Any]:
    """Parse TOML content from a string or bytes."""
    if isinstance(content, bytes):
        content = content.decode("utf-8")
    return tomllib.loads(content)
