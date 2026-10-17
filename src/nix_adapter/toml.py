"""TOML serialization and parsing utilities using standard library tomllib."""

from __future__ import annotations
import re
import tomllib
from typing import Any


def _format_key(key: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]+", key):
        return key
    escaped = key.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _dump_value(val: Any) -> str:
    if isinstance(val, bool):
        return "true" if val else "false"
    elif isinstance(val, int):
        return str(val)
    elif isinstance(val, float):
        s = str(val)
        return s if ("." in s or "e" in s.lower()) else f"{s}.0"
    elif isinstance(val, str):
        escaped = (
            val.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
        )
        return f'"{escaped}"'
    elif isinstance(val, list):
        items = [_dump_value(x) for x in val]
        return f"[{', '.join(items)}]"
    elif isinstance(val, dict):
        items = [f"{_format_key(k)} = {_dump_value(v)}" for k, v in val.items()]
        return f"{{{', '.join(items)}}}"
    raise TypeError(f"Unsupported TOML value type: {type(val)}")


def _is_table_array(val: Any) -> bool:
    return (
        isinstance(val, list) and len(val) > 0 and all(isinstance(x, dict) for x in val)
    )


def _dump_table(data: dict[str, Any], path: list[str]) -> list[str]:
    lines: list[str] = []

    # 1. Primitives & non-table lists
    for k, v in data.items():
        if isinstance(v, dict) or _is_table_array(v):
            continue
        lines.append(f"{_format_key(k)} = {_dump_value(v)}")

    # 2. Sub-tables
    for k, v in data.items():
        if isinstance(v, dict):
            new_path = path + [k]
            header = f"[{'.'.join(_format_key(p) for p in new_path)}]"
            if lines and lines[-1] != "":
                lines.append("")
            lines.append(header)
            lines.extend(_dump_table(v, new_path))

    # 3. Array of tables
    for k, v in data.items():
        if _is_table_array(v):
            new_path = path + [k]
            header = f"[[{'.'.join(_format_key(p) for p in new_path)}]]"
            for item in v:
                if lines and lines[-1] != "":
                    lines.append("")
                lines.append(header)
                lines.extend(_dump_table(item, new_path))

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
