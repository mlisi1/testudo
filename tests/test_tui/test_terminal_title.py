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


def test_window_icon_is_set_after_the_push_and_restored_before_the_pop(monkeypatch) -> None:
    from testudo.tui import window_icon

    out = _Tty()
    events: list[str] = []

    def fake_set_window_icon(show):
        show("marker")
        events.append("set")
        return lambda: events.append(f"restore after {len(out.getvalue())}")

    monkeypatch.setattr(window_icon, "set_window_icon", fake_set_window_icon)
    with terminal_title("testudo", out, window_icon=True):
        assert out.getvalue() == PUSH_TITLE + "\x1b]0;marker\x07" + "\x1b]0;testudo\x07"
    written_before_pop = len(out.getvalue()) - len(POP_TITLE)
    assert events == ["set", f"restore after {written_before_pop}"]
