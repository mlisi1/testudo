"""Unit tests for the Status Bar: duration formatting (pure function) plus
a pilot regression test for the uptime counter, which used to be computed
once at mount and never refreshed again (stuck at whatever it read at
startup, not a rosbag/replay artifact).
"""
from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult

from testudo.tui.widgets.header import TestudoHeader, format_duration


def test_format_duration_seconds_only() -> None:
    assert format_duration(45) == "45s"


def test_format_duration_minutes_and_seconds() -> None:
    assert format_duration(125) == "2m05s"


def test_format_duration_hours_minutes_seconds() -> None:
    assert format_duration(3723) == "1h02m03s"


def test_format_duration_zero() -> None:
    assert format_duration(0) == "0s"


class _HeaderOnlyApp(App):
    def compose(self) -> ComposeResult:
        yield TestudoHeader("jazzy")


def test_uptime_advances_on_its_own_without_any_external_poll() -> None:
    """Regression: uptime_seconds was only ever set once, in on_mount, so it
    read as permanently stuck at ~0s. The header must refresh it on its own
    recurring timer, independent of whatever the app's data-source poll does.
    """

    async def body() -> None:
        app = _HeaderOnlyApp()
        async with app.run_test() as pilot:
            header = app.query_one(TestudoHeader)
            first_reading = header.uptime_seconds
            await pilot.pause(1.2)
            assert header.uptime_seconds > first_reading

    asyncio.run(body())
