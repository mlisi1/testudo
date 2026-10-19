"""Detail Panel layout for `PointStreamPlugin`: pure functions, no state.

Split out of point_stream.py so the plugin module keeps to its checks:

```
returns  ████████████████████▓▓▓░  92% valid · 6% no return · 2% invalid · 0 out of range
profile  ▃▃▄▅▇██▇▅▄▃▂▁▁▁▂▃▅▆▇▇▆▅▄▃  −135° … +135°  0.10–30.0 m
```
"""
from __future__ import annotations

import math

import numpy as np

from testudo.plugins.base import Severity, colorize
from testudo.plugins.render import sparkline

_BAR_WIDTH = 24
_PROFILE_WIDTH = 48

#: (glyph, style) for each share of a `returns_bar`, in drawing order.
_VALID = ("█", "green")
_NO_RETURN = ("▓", "dim")
_OUT_OF_RANGE = ("▒", "yellow")
_INVALID = ("░", "bold red")


def returns_bar(valid: float, no_return: float, out_of_range: float, invalid: float) -> str:
    """A stacked bar of what fraction of beams/points fell in each class, each class in its own glyph.

    "No return" (+Inf on a scan, NaN on a non-dense cloud) is a valid
    reading -- nothing within range -- so it's dim, not red: the bar
    splits "invalid" honestly instead of lumping everything non-finite together.
    """
    shares = [(valid, _VALID), (no_return, _NO_RETURN), (out_of_range, _OUT_OF_RANGE), (invalid, _INVALID)]
    cells = [round(share * _BAR_WIDTH) for share, _ in shares]
    # Rounding can over/undershoot by a cell; settle the difference on the largest share.
    cells[cells.index(max(cells))] += _BAR_WIDTH - sum(cells)
    # Any nonzero share gets at least one cell, so a small problem is never invisible.
    for index, (share, _) in enumerate(shares):
        if share > 0 and cells[index] == 0:
            cells[index] = 1
            cells[cells.index(max(cells))] -= 1
    return "".join(f"[{style}]{glyph * count}[/{style}]" for count, (_, (glyph, style)) in zip(cells, shares) if count)


def returns_text(valid: float, no_return: float, out_of_range: float, invalid: float, invalid_severity: int) -> str:
    """The bar's legend: each share in words, with the invalid share colored by its check's verdict."""
    parts = [f"{valid:.0%} valid", f"[dim]{no_return:.0%} no return[/dim]"]
    if out_of_range > 0:
        parts.append(colorize(f"{out_of_range:.0%} out of range", Severity.WARN))
    parts.append(colorize(f"{invalid:.0%} invalid", invalid_severity))
    return " · ".join(parts)


def scan_profile(ranges: np.ndarray, range_max: float) -> list[float]:
    """Ranges bucketed by angle (mean per bucket) -- the scan's silhouette, start angle on the left.

    +Inf (no return) counts as `range_max`, i.e. "far"; NaN/-Inf buckets
    are NaN, which a sparkline draws as a gap.
    """
    if ranges.size == 0:
        return []
    readings = np.where(np.isposinf(ranges), range_max, ranges)
    readings = np.where(np.isfinite(readings), readings, np.nan)
    profile = []
    for bucket in np.array_split(readings, min(_PROFILE_WIDTH, readings.size)):
        finite = bucket[~np.isnan(bucket)]
        profile.append(float(finite.mean()) if finite.size else math.nan)
    return profile


def profile_line(profile: list[float], angle_min: float, angle_max: float, range_min: float, range_max: float) -> str:
    """The profile sparkline (scaled from 0, so a near wall reads low) with its angular and range span."""
    span = f"{math.degrees(angle_min):+.0f}° … {math.degrees(angle_max):+.0f}°  {range_min:.2f}–{range_max:.1f} m"
    return f"{sparkline(profile, floor=0.0)}  [dim]{span}[/dim]"


def format_points(count: int) -> str:
    """A point/beam count in at most five characters: "720", "29.2k", "1.31M"."""
    if count < 1000:
        return str(count)
    if count < 100_000:
        return f"{count / 1000:.1f}k"
    if count < 1_000_000:
        return f"{count / 1000:.0f}k"
    return f"{count / 1_000_000:.2f}M"
