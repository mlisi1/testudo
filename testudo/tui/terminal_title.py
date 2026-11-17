"""The terminal's window title while the TUI runs: `testudo`, the user's own title back on exit.

Textual sets no terminal title itself. xterm-compatible terminals (xterm,
VTE/GNOME Terminal, Konsole, kitty, Alacritty, tmux, ...) keep a title
stack: `CSI 22;0 t` pushes the current title, `CSI 23;0 t` pops it back,
so the title the user had before is restored without Testudo having to
read it. A terminal without the stack ignores both and keeps `testudo`
after exit, which is harmless.

Written straight to the real stdout before `App.run()` and after it
returns, not from inside the app: Textual owns the terminal in between,
and the pilot tests run the app with no terminal at all.
"""
from __future__ import annotations

import contextlib
import sys
from collections.abc import Iterator
from typing import TextIO

#: The live dashboard's title; `replay --watch` adds `REPLAY_SUFFIX`.
TITLE = "testudo"
REPLAY_SUFFIX = " · replay"

PUSH_TITLE = "\x1b[22;0t"
POP_TITLE = "\x1b[23;0t"


def set_title_sequence(title: str) -> str:
    """OSC 0 (window title and icon name) for `title`, control characters dropped."""
    safe = "".join(char for char in title if char.isprintable())
    return f"\x1b]0;{safe}\x07"


@contextlib.contextmanager
def terminal_title(title: str, stream: TextIO | None = None) -> Iterator[None]:
    """Show `title` as the terminal's title for the `with` block, then restore the previous one.

    `stream` defaults to the process's real stdout; nothing is written
    unless it's a terminal (piped or redirected output stays clean).
    """
    out = stream if stream is not None else sys.__stdout__
    if out is None or not out.isatty():
        yield
        return
    out.write(PUSH_TITLE + set_title_sequence(title))
    out.flush()
    try:
        yield
    finally:
        out.write(POP_TITLE)
        out.flush()
