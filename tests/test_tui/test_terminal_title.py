"""terminal_title: escape sequences written around the TUI, no real terminal needed."""
from __future__ import annotations

import io

from testudo.tui.terminal_title import POP_TITLE, PUSH_TITLE, set_title_sequence, terminal_title


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_title_is_pushed_set_and_popped_around_the_block() -> None:
    out = _Tty()
    with terminal_title("testudo", out):
        assert out.getvalue() == PUSH_TITLE + "\x1b]0;testudo\x07"
    assert out.getvalue().endswith(POP_TITLE)


def test_title_is_restored_when_the_block_raises() -> None:
    out = _Tty()
    try:
        with terminal_title("testudo", out):
            raise RuntimeError
    except RuntimeError:
        pass
    assert out.getvalue().endswith(POP_TITLE)


def test_nothing_is_written_when_not_a_terminal() -> None:
    out = io.StringIO()
    with terminal_title("testudo", out):
        pass
    assert out.getvalue() == ""


def test_control_characters_cannot_end_the_sequence_early() -> None:
    assert set_title_sequence("a\x07b\x1bc") == "\x1b]0;abc\x07"
