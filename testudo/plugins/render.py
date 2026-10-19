"""Small Rich-markup string helpers shared by plugins' `CheckStatus.values`.

One visual vocabulary across every plugin, so a user learns it once:

- `meter`: margin left before the next threshold zone (`▮▮▮▮▯▯`), colored by zone.
- `sparkline`: the last N samples of one metric (`▁▂▃▅▇`), colored by its current severity.
- `strip`: the last N discrete outcomes, newest right (`✓✓✗✓⊘`).
- `fmt_sigma` / `fmt_size`: human units for a variance and a byte count.

These are pure functions returning Rich markup (or plain text, for the
`fmt_*` ones). They are *not* part of the `CheckPlugin` contract, just a
convenience a plugin author is free to use or ignore. Severity colors come
from `SEVERITY_COLORS`, never the brand palette: color means status here.
"""
from __future__ import annotations

import math
from collections.abc import Iterable

from testudo.plugins.base import SEVERITY_COLORS, Severity, ThresholdZone, evaluate_zone

EMPTY_SQUARE = "▯"
FILLED_SQUARE = "▮"

#: When a zone doesn't configure `red` (none of the built-in defaults do),
#: a meter's "empty" reference is `orange` scaled by this factor instead.
METER_FALLBACK_SCALE = 2.0

SPARK_LEVELS = "▁▂▃▄▅▆▇█"

#: Glyph per outcome severity in a `strip`; `None` is a neutral event
#: (e.g. a canceled goal) -- neither a success nor a failure.
_STRIP_GLYPHS = {
    Severity.OK: "✓",
    Severity.WARN: "▲",
    Severity.ERROR: "✗",
    Severity.STALE: "■",
}
_STRIP_NEUTRAL_GLYPH = "⊘"


def meter(value: float, zone: ThresholdZone | None, segments: int) -> str:
    """A `segments`-wide health-bar-style meter for `value` against `zone`.

    Full and green means good; as `value` gets worse the bar drains and
    turns orange, then red -- the fill level *and* its one color both track
    the same thing (how much margin is left), rather than a per-segment
    gradient. Never drains to fully empty (`value` far past `zone.orange`/
    `zone.red` still leaves one square filled) so a bad reading is legible
    as "almost empty and red", not indistinguishable from no data.
    `segments=1` collapses this to that one square, colored the same way
    (the narrow-Detail-Panel fallback). Empty string when `zone` has no
    `orange` boundary to measure against.
    """
    if zone is None or zone.orange is None:
        return ""
    color = SEVERITY_COLORS.get(evaluate_zone(value, zone), "white")
    if segments <= 1:
        return f"[{color}]{FILLED_SQUARE}[/{color}]"
    span = zone.red if zone.red is not None else zone.orange * METER_FALLBACK_SCALE
    remaining = 1.0 - value / span if span > 0 else 0.0
    filled = max(1, min(segments, round(remaining * segments)))
    return f"[{color}]{FILLED_SQUARE * filled}[/{color}]{EMPTY_SQUARE * (segments - filled)}"


def downsample(samples: Iterable[float], width: int) -> list[float]:
    """`samples` averaged into at most `width` consecutive chunks (non-finite ones skipped; all-NaN chunk -> NaN).

    Lets a long history (60 one-second samples) fit a short sparkline while
    still spanning all of it, rather than keeping only its last `width`.
    """
    values = list(samples)
    if len(values) <= width or width <= 0:
        return values
    chunks = [values[len(values) * i // width : len(values) * (i + 1) // width] for i in range(width)]
    averaged = []
    for chunk in chunks:
        finite = [v for v in chunk if math.isfinite(v)]
        averaged.append(sum(finite) / len(finite) if finite else math.nan)
    return averaged


def sparkline(
    samples: Iterable[float],
    severity: int = Severity.OK,
    width: int | None = None,
    floor: float | None = None,
    min_range: float | None = None,
) -> str:
    """The last `width` (default: all) of `samples` as `▁..█` bars, colored by `severity`.

    Scaled between the window's own min and max, so it shows shape (a dip,
    a spike, a ramp), not absolute level -- pair it with the current value.
    `floor` (e.g. 0 for a σ or a rate) pins the bottom of the scale instead,
    so small noise on a large steady value reads as steady, not as spikes.
    `min_range` keeps the scale at least that tall (extending it upward),
    so a swing that small in absolute terms -- 0.0↔0.1 % CPU, a 1 MB
    wobble in RSS -- stays near the bottom instead of filling the height.
    A flat window renders as a flat mid-height line rather than all-bottom,
    so "steady" doesn't read as "zero" -- unless it sits right at `floor`,
    where all-bottom is exactly what it means. Non-finite samples render as a
    space (a gap). Empty string for no samples.
    """
    values = list(samples)
    if width is not None:
        values = values[-width:] if width > 0 else []
    if not values:
        return ""
    finite = [v for v in values if math.isfinite(v)]
    low, high = (min(finite), max(finite)) if finite else (0.0, 0.0)
    if floor is not None and finite:
        low = min(low, floor)
    if min_range is not None and finite:
        high = max(high, low + min_range)
    top = len(SPARK_LEVELS) - 1
    chars = []
    for value in values:
        if not math.isfinite(value):
            chars.append(" ")
        elif high - low <= 0:
            chars.append(SPARK_LEVELS[0] if floor is not None and value <= floor else SPARK_LEVELS[top // 2])
        else:
            chars.append(SPARK_LEVELS[round((value - low) / (high - low) * top)])
    color = SEVERITY_COLORS.get(severity, "white")
    return f"[{color}]{''.join(chars)}[/{color}]"


def strip(outcomes: Iterable[int | None], width: int | None = None) -> str:
    """The last `width` (default: all) discrete outcomes as glyphs, newest right.

    Each outcome is a `Severity` (`✓` OK, `▲` WARN, `✗` ERROR, `■` STALE,
    each in its severity color) or `None` for a neutral event (dim `⊘`).
    Empty string for no outcomes.
    """
    events = list(outcomes)
    if width is not None:
        events = events[-width:] if width > 0 else []
    parts = []
    for outcome in events:
        if outcome is None:
            parts.append(f"[dim]{_STRIP_NEUTRAL_GLYPH}[/dim]")
            continue
        color = SEVERITY_COLORS.get(outcome, "white")
        parts.append(f"[{color}]{_STRIP_GLYPHS.get(outcome, '?')}[/{color}]")
    return "".join(parts)


def fmt_sigma(variance: float, unit: str = "m") -> str:
    """A variance as its standard deviation, e.g. `0.0676` -> `±0.26m`; `n/a` if negative/non-finite.

    Two decimals below 10 (so a column of them lines up), whole units above.
    """
    if not math.isfinite(variance) or variance < 0:
        return "n/a"
    sigma = math.sqrt(variance)
    return f"±{sigma:.2f}{unit}" if sigma < 10 else f"±{sigma:.0f}{unit}"


def fmt_size(num_bytes: float) -> str:
    """A byte count as KB below 1 MB, else MB -- e.g. `12.3KB`, `1.50MB`."""
    kb = num_bytes / 1024.0
    return f"{kb:.1f}KB" if kb < 1024 else f"{kb / 1024.0:.2f}MB"
