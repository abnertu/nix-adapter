"""Shared, best-effort task presentation; callers retain ownership of work and I/O."""

from __future__ import annotations

import contextlib
import os
import sys
import time
import unicodedata
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import TracebackType
from typing import IO

from rich.console import Console, RenderableType
from rich.live import Live
from rich.progress_bar import ProgressBar
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text


def _clean(value: str) -> str:
    return "".join(
        " " if unicodedata.category(char).startswith("C") else char for char in value
    )


STATUS_STYLES = {"done": "green", "skip": "yellow", "failed": "red"}
STATUS_ICONS = {"done": "✓", "skip": "↷", "failed": "✗"}
STATUS_ASCII_ICONS = {"done": "+", "skip": "-", "failed": "!"}
COMPACT_WIDTH = 38
"""Below this width a task renders as one plain line."""
PROGRESS_BAR_WIDTH = 60
"""From this width a task with a total also shows a progress bar."""


@dataclass
class Task:
    """One running task line; finish it exactly once."""

    _owner: Progress
    label: str
    total: int | None = None
    completed: int = 0
    started: float = field(default_factory=time.monotonic)
    _finished: bool = False
    spinner: Spinner = field(default_factory=lambda: Spinner("dots", style="cyan"))

    def update(self, completed: int, total: int | None = None) -> None:
        """Report progress; values are clamped to `[0, total]`."""
        if self._finished:
            return
        if total is not None:
            self.total = max(0, total)
        self.completed = max(0, completed)
        if self.total is not None:
            self.completed = min(self.completed, self.total)
        self._owner.refresh()

    def finish(self, status: str = "done") -> None:
        """End the task as `done`, `skip` or `failed`; later calls do nothing."""
        if not self._finished:
            self._finished = True
            self._owner.finish_task(self, status)

    def external_output(self):
        """Pause the display while a child process writes to the terminal."""
        return self._owner.external_output()


class Progress:
    """One renderer for sequential/nested tasks, always separate from child I/O.

    Presentation failures disable the display and never replace a work result.
    A task context records exceptions without inspecting their contents.
    """

    def __init__(
        self, scope: str = "", verbose: bool = False, *, stream: IO[str] | None = None
    ):
        self.scope = _clean(scope)
        self.stream = stream if stream is not None else sys.stderr
        self._tasks: list[Task] = []
        self._live: Live | None = None
        self._suspended = 0
        self._disabled = False
        self._closed = False
        try:
            terminal = self.stream.isatty()
        except Exception:
            terminal = False
            self._disabled = True
        self._animated = (
            terminal
            and not verbose
            and (os.environ.get("TERM") or "dumb") != "dumb"
            and "NO_COLOR" not in os.environ
            and not os.environ.get("CI")
        )
        self._unicode = "utf" in (getattr(self.stream, "encoding", "") or "").lower()
        self._console = Console(
            file=self.stream,
            force_terminal=self._animated,
            color_system="auto" if self._animated else None,
            no_color=not self._animated,
            highlight=False,
            markup=False,
        )

    def __enter__(self) -> Progress:
        return self

    def __exit__(
        self,
        _kind: type[BaseException] | None,
        _error: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _safe(self, operation: Callable[[], object]) -> None:
        if self._disabled:
            return
        try:
            operation()
        except Exception:
            self._disabled = True
            # Stop Rich's refresh thread even if the stream stopped accepting
            # writes. No exception details or task payloads are diagnostic data.
            live, self._live = self._live, None
            if live is not None:
                with contextlib.suppress(Exception):
                    live.stop()

    def active(self) -> Task | None:
        """The innermost running task."""
        return self._tasks[-1] if self._tasks else None

    def _row(
        self, task: Task | None = None, status: str | None = None
    ) -> RenderableType:
        current = task or self.active()
        if current is None:
            return Text("")
        width = max(1, self._console.width - 1)
        elapsed = f"{max(0.0, time.monotonic() - current.started):.1f}s"
        label = Text(current.label, style=STATUS_STYLES.get(status or "", "bold"))
        if width < COMPACT_WIDTH:
            return self._compact_row(label, elapsed, status, width)
        return self._table_row(current, label, elapsed, status, width)

    def _icons(self) -> dict[str, str]:
        return STATUS_ICONS if self._unicode else STATUS_ASCII_ICONS

    def _compact_row(
        self, label: Text, elapsed: str, status: str | None, width: int
    ) -> Text:
        running = "·" if self._unicode else "*"
        line = Text(f"{self._icons().get(status or '', running)} ")
        line.append_text(label)
        line.append(f"  {elapsed}", style="dim")
        line.truncate(width, overflow="ellipsis" if self._unicode else "crop")
        return line

    def _table_row(
        self, task: Task, label: Text, elapsed: str, status: str | None, width: int
    ) -> Table:
        show_bar = (
            status is None and task.total is not None and width >= PROGRESS_BAR_WIDTH
        )
        table = Table.grid(expand=True, padding=(0, 1))
        table.width = width
        table.add_column(width=1)
        table.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
        if show_bar:
            table.add_column(width=min(22, width // 4))
            table.add_column(justify="right", no_wrap=True)
        table.add_column(justify="right", no_wrap=True)
        icon: RenderableType = task.spinner
        if status:
            icon = Text(self._icons()[status], style=STATUS_STYLES[status])
        cells: list[RenderableType] = [icon, label]
        if show_bar and task.total is not None:  # Repeated for type narrowing.
            cells += [
                self._bar(task, task.total),
                Text(f"{task.completed}/{task.total}", style="cyan"),
            ]
        cells.append(Text(elapsed, style="dim"))
        table.add_row(*cells)
        return table

    @staticmethod
    def _bar(task: Task, total: int) -> ProgressBar:
        return ProgressBar(
            total=max(1, total),
            completed=task.completed,
            style="grey23",
            complete_style="cyan",
            finished_style="green",
        )

    def _stop(self) -> None:
        live, self._live = self._live, None
        if live is not None:
            live.stop()

    def refresh(self) -> None:
        """Redraw the active task, or stop the display when none is shown."""

        def render() -> None:
            if not self._animated or self._suspended or self._closed or not self._tasks:
                self._stop()
                return
            if self._live is None:
                self._live = Live(
                    console=self._console,
                    get_renderable=self._row,
                    refresh_per_second=10,
                    transient=True,
                    screen=False,
                    redirect_stdout=False,
                    redirect_stderr=False,
                )
                self._live.start(refresh=True)
            else:
                self._live.refresh()

        self._safe(render)

    def _plain(self, task: Task, status: str) -> None:
        # Logs retain the complete task label, without terminal-width wrapping.
        elapsed = max(0.0, time.monotonic() - task.started)
        self.stream.write(f"[{status}] {task.label} ({elapsed:.1f}s)\n")
        self.stream.flush()

    def start(self, label: str, total: int | None = None) -> Task:
        """Start a task; prefer the `task` context manager."""
        task = Task(self, _clean(label), max(0, total) if total is not None else None)
        if not self._unicode:
            task.spinner = Spinner("line", style="cyan")
        self._tasks.append(task)
        if not self._animated:
            self._safe(lambda: self._plain(task, "start"))
        self.refresh()
        return task

    @contextmanager
    def task(self, label: str, total: int | None = None) -> Generator[Task]:
        """Run a task block; it finishes `failed` if the block raises."""
        task = self.start(label, total)
        try:
            yield task
        except BaseException:
            task.finish("failed")
            raise
        else:
            task.finish("done")

    def finish_task(self, task: Task, status: str) -> None:
        """Print the final line of `task`; called by `Task.finish`."""
        status = status if status in {"done", "skip", "failed"} else "failed"
        self._safe(self._stop)
        if task in self._tasks:
            self._tasks.remove(task)
        if self._animated:
            self._safe(lambda: self._console.print(self._row(task, status)))
        else:
            self._safe(lambda: self._plain(task, status))
        self.refresh()

    def suspend(self) -> None:
        """Hide the display until a matching `resume`."""
        self._suspended += 1
        self._safe(self._stop)

    def resume(self) -> None:
        """Undo one `suspend`."""
        self._suspended = max(0, self._suspended - 1)
        self.refresh()

    @contextmanager
    def external_output(self):
        """Yield the stream with the display hidden, for child process output."""
        self.suspend()
        try:
            yield self.stream
        finally:
            self.resume()

    def close(self) -> None:
        """Fail the unfinished tasks and stop the display."""
        self._closed = True
        for task in reversed(self._tasks.copy()):
            task.finish("failed")
        self._safe(self._stop)
