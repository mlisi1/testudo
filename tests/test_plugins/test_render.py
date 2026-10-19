"""Unit tests for the shared plugin rendering helpers -- pure strings, no ROS."""
from __future__ import annotations

from rich.text import Text

from testudo.plugins.base import Severity, ThresholdZone
from testudo.plugins.render import downsample, fmt_sigma, fmt_size, meter, sparkline, strip


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
    assert fmt_sigma(0.01, "°") == "±0.10°"
    assert fmt_sigma(0.0) == "±0.00m"
    assert fmt_sigma(-1.0) == "n/a"
    assert fmt_sigma(float("nan")) == "n/a"
    assert fmt_size(512) == "0.5KB"
    assert fmt_size(3 * 1024 * 1024) == "3.00MB"


def test_sparkline_floor_keeps_noise_on_a_steady_value_flat() -> None:
    assert _plain(sparkline([10.0, 10.2, 9.9], floor=0.0)) == "███"
    assert _plain(sparkline([10.0, 10.2, 9.9])) != "███"


def test_sparkline_min_range_keeps_small_swings_low() -> None:
    assert _plain(sparkline([0.0, 0.1, 0.0], floor=0.0)) == "▁█▁"
    assert _plain(sparkline([0.0, 0.1, 0.0], floor=0.0, min_range=100.0)) == "▁▁▁"
    assert _plain(sparkline([0.0, 100.0], floor=0.0, min_range=10.0)) == "▁█"


def test_sparkline_flat_at_floor_is_all_bottom() -> None:
    assert _plain(sparkline([0.0, 0.0, 0.0], floor=0.0)) == "▁▁▁"


def test_downsample_averages_consecutive_chunks() -> None:
    assert downsample([1.0, 3.0, 5.0, 7.0], 2) == [2.0, 6.0]
    assert downsample([1.0, 2.0], 5) == [1.0, 2.0]
    assert downsample(range(60), 20)[0] == 1.0
    nan_chunk = downsample([float("nan"), float("nan"), 1.0, 1.0], 2)
    assert nan_chunk[1] == 1.0 and nan_chunk[0] != nan_chunk[0]
