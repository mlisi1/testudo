"""Keeps stray log output from corrupting a live Textual session.

Every check in this codebase already logs through `logging`, never
`print()` (ground rule 7) -- but that alone doesn't stop a live `watch`
session from flickering. `logging.basicConfig`'s default `StreamHandler`
writes straight to the terminal's own file descriptor, and Textual's
rendering has no way to know that happened -- it doesn't intercept writes
made outside its own drawing calls. A background thread logging
"could not resolve message class" (the most common cause in practice: a
message type whose interface package isn't installed on this host) while
the TUI is mid-render corrupts the display just as surely as a stray
`print()` would.

`suppressed_terminal_logging` swaps the root logger's handlers out for a
`LogSummaryHandler` for exactly the span where a Textual app owns the
terminal, then restores the originals and prints what it caught -- once
the terminal is safe to write to again.
"""
from __future__ import annotations

import contextlib
import logging
from typing import Iterator

from rich.console import Console


class LogSummaryHandler(logging.Handler):
    """Collects (level, message) counts instead of writing them anywhere."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self._counts: dict[tuple[int, str], int] = {}

    def emit(self, record: logging.LogRecord) -> None:
        # `record.getMessage()`, not `self.format(record)` -- the latter
        # appends a full traceback for a `logger.exception(...)` call
        # (e.g. subscription_manager's "could not resolve message
        # class"), which would make the closing summary as noisy as the
        # live output it's replacing. One line per distinct message is
        # the point; the traceback is still available with `-v` piped to
        # a file, for whoever needs to actually debug it.
        key = (record.levelno, record.getMessage())
        self._counts[key] = self._counts.get(key, 0) + 1

    def summary_lines(self) -> list[str]:
        """One line per distinct (level, message), worst severity first, with a repeat count."""
        return [
            f"{logging.getLevelName(level)}: {message}" + (f" (x{count})" if count > 1 else "")
            for (level, message), count in sorted(self._counts.items(), key=lambda item: -item[0][0])
        ]


@contextlib.contextmanager
def suppressed_terminal_logging() -> Iterator[LogSummaryHandler]:
    """Buffer WARNING+ log records for the duration of the `with` block, then print a summary.

    Wrap exactly the span during which a Textual app has the terminal --
    from just before anything that might log (a background spin thread,
    the app itself) starts, through `app.run()` returning -- not the
    whole command. Silent when nothing was caught, since most sessions
    never hit this at all.
    """
    root_logger = logging.getLogger()
    handler = LogSummaryHandler()
    previous_handlers = root_logger.handlers[:]
    root_logger.handlers = [handler]
    try:
        yield handler
    finally:
        root_logger.handlers = previous_handlers
        _print_summary(handler)


def _print_summary(handler: LogSummaryHandler) -> None:
    lines = handler.summary_lines()
    if not lines:
        return
    console = Console()
    console.print()
    console.print(f"[bold yellow]{len(lines)} warning/error message(s) suppressed during the session:[/bold yellow]")
    for line in lines:
        console.print(f"  {line}")
