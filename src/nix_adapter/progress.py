"""Shared, best-effort task presentation; callers retain ownership of work and I/O."""

from __future__ import annotations

import argparse
import base64
import contextlib
import json
import os
import signal
import sys
import time
import unicodedata
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import FrameType, TracebackType
from typing import IO, NoReturn

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


@dataclass
class Task:
    _owner: Progress
    label: str
    total: int | None = None
    completed: int = 0
    started: float = field(default_factory=time.monotonic)
    _finished: bool = False
    spinner: Spinner = field(default_factory=lambda: Spinner("dots", style="cyan"))

    def update(self, completed: int, total: int | None = None) -> None:
        if self._finished:
            return
        if total is not None:
            self.total = max(0, total)
        self.completed = max(0, completed)
        if self.total is not None:
            self.completed = min(self.completed, self.total)
        self._owner.refresh()

    def finish(self, status: str = "done") -> None:
        if not self._finished:
            self._finished = True
            self._owner.finish_task(self, status)

    def external_output(self):
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
        return self._tasks[-1] if self._tasks else None

    def _row(
        self, task: Task | None = None, status: str | None = None
    ) -> RenderableType:
        current = task or self.active()
        if current is None:
            return Text("")
        styles = {"done": "green", "skip": "yellow", "failed": "red"}
        icons = {"done": "✓", "skip": "↷", "failed": "✗"}
        ascii_icons = {"done": "+", "skip": "-", "failed": "!"}
        width = max(1, self._console.width - 1)
        elapsed = f"{max(0.0, time.monotonic() - current.started):.1f}s"
        label = Text(current.label, style=styles.get(status or "", "bold"))
        if width < 38:
            symbols = icons if self._unicode else ascii_icons
            line = Text(f"{symbols.get(status or '', '·' if self._unicode else '*')} ")
            line.append_text(label)
            line.append(f"  {elapsed}", style="dim")
            line.truncate(width, overflow="ellipsis" if self._unicode else "crop")
            return line
        table = Table.grid(expand=True, padding=(0, 1))
        table.width = width
        table.add_column(width=1)
        table.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
        if status is None and current.total is not None and width >= 60:
            table.add_column(width=min(22, width // 4))
            table.add_column(justify="right", no_wrap=True)
        table.add_column(justify="right", no_wrap=True)
        if status:
            icon: RenderableType = Text(
                icons[status] if self._unicode else ascii_icons[status],
                style=styles[status],
            )
        else:
            icon = current.spinner
        cells: list[RenderableType] = [icon, label]
        if status is None and current.total is not None and width >= 60:
            cells.extend(
                [
                    ProgressBar(
                        total=max(1, current.total),
                        completed=current.completed,
                        style="grey23",
                        complete_style="cyan",
                        finished_style="green",
                    ),
                    Text(f"{current.completed}/{current.total}", style="cyan"),
                ]
            )
        cells.append(Text(elapsed, style="dim"))
        table.add_row(*cells)
        return table

    def _stop(self) -> None:
        live, self._live = self._live, None
        if live is not None:
            live.stop()

    def refresh(self) -> None:
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
        task = self.start(label, total)
        try:
            yield task
        except BaseException:
            task.finish("failed")
            raise
        else:
            task.finish("done")

    def finish_task(self, task: Task, status: str) -> None:
        status = status if status in {"done", "skip", "failed"} else "failed"
        self._safe(self._stop)
        if task in self._tasks:
            self._tasks.remove(task)
        if self._animated:
            self._safe(lambda: self._console.print(self._row(task, status)))
        else:
            self._safe(lambda: self._plain(task, status))
        self.refresh()

    def update(self, completed: int, total: int | None = None) -> None:
        if task := self.active():
            task.update(completed, total)

    def finish(self, status: str = "done") -> None:
        if task := self.active():
            task.finish(status)

    def suspend(self) -> None:
        self._suspended += 1
        self._safe(self._stop)

    def resume(self) -> None:
        self._suspended = max(0, self._suspended - 1)
        self.refresh()

    @contextmanager
    def external_output(self):
        self.suspend()
        try:
            yield self.stream
        finally:
            self.resume()

    def close(self) -> None:
        self._closed = True
        for task in reversed(self._tasks.copy()):
            task.finish("failed")
        self._safe(self._stop)


def _renderer_main(verbose: bool) -> int:
    ui = Progress(verbose=verbose)

    def stop(_signal: int, _frame: FrameType | None) -> NoReturn:
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        for line in sys.stdin:
            identifier = None
            closing = False
            try:
                event = json.loads(line)
                identifier = event.get("id")
                kind = event.get("event")
                if kind == "start":
                    if ui.active():
                        ui.finish("failed")
                    label = event.get("label", "")
                    if "label_b64" in event:
                        label = base64.b64decode(
                            event["label_b64"], validate=True
                        ).decode("utf-8", "replace")
                    total = event.get("total")
                    if not isinstance(label, str) or (
                        total is not None and type(total) is not int
                    ):
                        raise ValueError("invalid event")
                    ui.start(label, total)
                elif kind == "update":
                    completed, total = event.get("completed"), event.get("total")
                    if type(completed) is not int or (
                        total is not None and type(total) is not int
                    ):
                        raise ValueError("invalid event")
                    ui.update(completed, total)
                elif kind == "finish":
                    ui.finish(event.get("status", "failed"))
                elif kind == "suspend":
                    ui.suspend()
                elif kind == "resume":
                    ui.resume()
                elif kind == "close":
                    ui.close()
                    closing = True
            except (ValueError, TypeError, AttributeError, KeyError):
                # Malformed UI events contain no useful task failure diagnostics.
                ui.close()
            if type(identifier) is int:
                sys.stdout.write(f"{identifier}\n")
                sys.stdout.flush()
            if closing:
                break
    except (BrokenPipeError, OSError):
        pass
    finally:
        ui.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--renderer", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    return _renderer_main(args.verbose) if args.renderer else 0


if __name__ == "__main__":
    raise SystemExit(main())
