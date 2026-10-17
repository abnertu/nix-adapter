"""Plan output: what converge would change, reported without changing anything."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

# = unchanged, ~ change, + addition, ! conflict, - skipped. The symbol carries
# the meaning; color only emphasizes it on a terminal.
Kind = Literal["=", "~", "+", "!", "-"]
STYLES: dict[str, str] = {"=": "2", "~": "33", "+": "32", "!": "1;31", "-": "1;33"}


@dataclass(frozen=True)
class Change:
    kind: Kind
    text: str


def render(changes: Iterable[Change], name: str, *, color: bool = False) -> str:
    """One line per change, then a summary of what would be updated."""
    lines: list[str] = []
    pending = 0
    for change in changes:
        line = f"{change.kind} {change.text}"
        lines.append(f"\033[{STYLES[change.kind]}m{line}\033[0m" if color else line)
        pending += change.kind in ("~", "+")
    summary = f"{name} plan: {pending} changes."
    lines.append(f"\033[1m{summary}\033[0m" if color else summary)
    return "\n".join(lines)
