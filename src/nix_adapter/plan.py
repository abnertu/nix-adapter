"""Plan output: what converge would change, reported without changing anything."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

# = unchanged, ~ change, + addition, ! conflict, - skipped. The symbol carries
# the meaning; color only emphasizes it on a terminal.
Kind = Literal["=", "~", "+", "!", "-"]
STYLES: dict[str, str] = {"=": "2", "~": "33", "+": "32", "!": "1;31", "-": "1;33"}
"""The SGR color code of each change kind, for callers that print plan lines."""

PENDING_KINDS = ("~", "+")


@dataclass(frozen=True)
class Change:
    """One planned outcome: its kind and a line that names the resource."""

    kind: Kind
    text: str


def styled(text: str, style: str, *, color: bool) -> str:
    """`text` wrapped in the SGR `style` when `color` is set."""
    return f"\033[{style}m{text}\033[0m" if color else text


def render(changes: Iterable[Change], name: str, *, color: bool = False) -> str:
    """One line per change, then a summary of what would be updated."""
    lines: list[str] = []
    pending = 0
    for change in changes:
        line = f"{change.kind} {change.text}"
        lines.append(styled(line, STYLES[change.kind], color=color))
        pending += change.kind in PENDING_KINDS
    lines.append(styled(f"{name} plan: {pending} changes.", "1", color=color))
    return "\n".join(lines)
