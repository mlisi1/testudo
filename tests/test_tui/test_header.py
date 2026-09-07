"""Unit test for the header's duration formatting -- pure function."""
from __future__ import annotations

from testudo.tui.widgets.header import format_duration


def test_format_duration_seconds_only() -> None:
    assert format_duration(45) == "45s"


def test_format_duration_minutes_and_seconds() -> None:
    assert format_duration(125) == "2m05s"


def test_format_duration_hours_minutes_seconds() -> None:
    assert format_duration(3723) == "1h02m03s"


def test_format_duration_zero() -> None:
    assert format_duration(0) == "0s"
