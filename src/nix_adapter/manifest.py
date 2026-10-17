"""Check a Nix-generated manifest against the adapter's TypedDict schema."""

from __future__ import annotations

from typing import Any, cast

from .exceptions import Conflict


def validate[T](schema: type[T], data: Any, name: str = "manifest") -> T:
    """Return `data` typed as `schema` if its top-level keys match exactly.

    A missing or unknown key means Nix and the adapter disagree about the
    manifest, so deployment stops before reading any value.
    """
    if not isinstance(data, dict):
        raise Conflict(f"{name} is not a JSON object")
    keys = set(cast("dict[str, Any]", data))
    required: frozenset[str] = getattr(schema, "__required_keys__", frozenset())
    optional: frozenset[str] = getattr(schema, "__optional_keys__", frozenset())
    missing = sorted(required - keys)
    unknown = sorted(keys - required - optional)
    if missing or unknown:
        details = [
            *(["missing " + ", ".join(missing)] if missing else []),
            *(["unknown " + ", ".join(unknown)] if unknown else []),
        ]
        raise Conflict(f"{name} does not match the adapter: {'; '.join(details)}")
    return cast(T, data)
