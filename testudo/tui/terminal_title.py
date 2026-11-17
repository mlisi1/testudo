"""The terminal's window title (and, on X11, icon) while the TUI runs; the user's own back on exit.

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
def terminal_title(title: str, stream: TextIO | None = None, *, window_icon: bool = False) -> Iterator[None]:
    """Show `title` as the terminal's title for the `with` block, then restore the previous one.

    `stream` defaults to the process's real stdout; nothing is written
    unless it's a terminal (piped or redirected output stays clean).
    `window_icon` also sets the shield as the window's icon, best effort
    (`window_icon.set_window_icon`, X11 only), restored first on the way out.
    """
    out = stream if stream is not None else sys.__stdout__
    if out is None or not out.isatty():
        yield
        return

    def show(text: str) -> None:
        out.write(set_title_sequence(text))
        out.flush()

    out.write(PUSH_TITLE)
    restore_icon = None
    if window_icon:
        from testudo.tui.window_icon import set_window_icon

        restore_icon = set_window_icon(show)
    show(title)
    try:
        yield
    finally:
        if restore_icon is not None:
            restore_icon()
        out.write(POP_TITLE)
        out.flush()
