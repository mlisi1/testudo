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


def test_weighted_health_maps_score_to_fraction() -> None:
    from testudo.plugins.base import CheckStatus, Severity
    from testudo.tui.widgets.header import weighted_health

    assert weighted_health(CheckStatus(Severity.OK, "overall", "", values={"weighted_score": "0"})) == 1.0
    assert weighted_health(CheckStatus(Severity.OK, "overall", "", values={"weighted_score": "0.5"})) == 0.75
    assert weighted_health(CheckStatus(Severity.STALE, "overall", "", values={"weighted_score": "3"})) == 0.0
    assert weighted_health(CheckStatus(Severity.OK, "overall", "")) is None


def test_health_gauge_is_fixed_width() -> None:
    from testudo.tui.widgets.header import health_gauge

    assert health_gauge(1.0) == "▕██████████▏"
    assert health_gauge(0.0) == "▕          ▏"
    assert health_gauge(0.81) == "▕████████▏ ▏"
    assert len(health_gauge(0.37)) == 12


def test_verdict_markup_lists_non_ok_counts_and_gauge_only_when_weighted() -> None:
    from rich.text import Text

    from testudo.plugins.base import CheckStatus, Severity
    from testudo.tui.widgets.header import verdict_markup

    counts = {Severity.OK: 30, Severity.ERROR: 6, Severity.WARN: 3}
    worst = CheckStatus(Severity.ERROR, "overall", "")
    assert Text.from_markup(verdict_markup(worst, counts, wide=True)).plain == "✗ FAULT ✗6 ▲3"
    both = CheckStatus(Severity.ERROR, "overall", "", values={"weighted_score": "0.38"})
    assert Text.from_markup(verdict_markup(both, counts, wide=False)).plain == "✗ FAULT ✗6 ▲3  81%"
    assert "weighted ▕" in Text.from_markup(verdict_markup(both, counts, wide=True)).plain
