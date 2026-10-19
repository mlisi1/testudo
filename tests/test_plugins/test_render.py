"""Unit tests for the shared plugin rendering helpers -- pure strings, no ROS."""
from __future__ import annotations

from rich.text import Text

from testudo.plugins.base import Severity, ThresholdZone
from testudo.plugins.render import fmt_sigma, fmt_size, meter, sparkline, strip


def _plain(markup: str) -> str:
    return Text.from_markup(markup).plain


def test_meter_is_full_and_green_when_nominal() -> None:
    assert meter(0.0, ThresholdZone(green=0.01, orange=0.1), 6) == f"[green]{'▮' * 6}[/green]"


def test_meter_is_almost_empty_and_red_when_far_past_orange() -> None:
    assert meter(10.0, ThresholdZone(green=0.01, orange=0.1), 6) == f"[bold red]▮[/bold red]{'▯' * 5}"


def test_meter_collapses_to_one_square_and_is_empty_without_orange() -> None:
    assert _plain(meter(0.0, ThresholdZone(green=0.01, orange=0.1), 1)) == "▮"
    assert meter(0.0, ThresholdZone(green=0.01), 6) == ""
    assert meter(0.0, None, 6) == ""


def test_sparkline_scales_to_window_and_keeps_width() -> None:
    assert _plain(sparkline([0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0])) == "▁▂▃▄▅▆▇█"
    assert sparkline(range(100), width=10) == sparkline(range(90, 100))


def test_sparkline_flat_is_mid_height_and_gaps_non_finite() -> None:
    assert _plain(sparkline([3.0, 3.0, 3.0])) == "▄▄▄"
    assert _plain(sparkline([0.0, float("nan"), 1.0])) == "▁ █"
    assert sparkline([]) == ""


def test_sparkline_colored_by_severity() -> None:
    assert sparkline([1.0, 2.0], Severity.ERROR).startswith("[bold red]")


def test_strip_glyphs_newest_right_and_width() -> None:
    outcomes = [Severity.OK, Severity.ERROR, None, Severity.OK]
    assert _plain(strip(outcomes)) == "✓✗⊘✓"
    assert _plain(strip(outcomes, width=2)) == "⊘✓"
    assert strip([]) == ""


def test_fmt_sigma_and_size() -> None:
    assert fmt_sigma(0.0676) == "±0.26m"
    assert fmt_sigma(400.0) == "±20m"
    assert fmt_sigma(0.01, "°") == "±0.1°"
    assert fmt_sigma(-1.0) == "n/a"
    assert fmt_sigma(float("nan")) == "n/a"
    assert fmt_size(512) == "0.5KB"
    assert fmt_size(3 * 1024 * 1024) == "3.00MB"
