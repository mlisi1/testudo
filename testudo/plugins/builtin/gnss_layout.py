"""Detail Panel layout for `GnssPlugin`: pure string builders, no state.

Split out of gnss.py so the plugin module keeps to its checks:

```
● GBAS_FIX   σ horiz ±0.26m ▮▮▯▯▯▯   σ vert ±0 (not reported)   cov known
fixes  ▮▮▮▮▮▮▮▮▮▮▮▮▮▮▮▮▯▮▮▮  1/20 dropped   jump 0.03 m/s ▮▮▮▮▮▮
```
"""
from __future__ import annotations

import math
from collections.abc import Iterable

from sensor_msgs.msg import NavSatStatus

from testudo.plugins.base import Severity, ThresholdZone, colorize
from testudo.plugins.render import EMPTY_SQUARE, FILLED_SQUARE, fmt_sigma, meter

#: NavSatStatus.service bits -> constellation names.
_SERVICES = (
    (NavSatStatus.SERVICE_GPS, "GPS"),
    (NavSatStatus.SERVICE_GLONASS, "GLO"),
    (NavSatStatus.SERVICE_COMPASS, "BDS"),
    (NavSatStatus.SERVICE_GALILEO, "GAL"),
)


def short_fix_label(fix_label: str) -> str:
    """"GBAS_FIX" -> "GBAS", "SBAS_FIX" -> "SBAS"; "FIX"/"NO_FIX" unchanged -- for the narrow Fix column."""
    return fix_label[: -len("_FIX")] if fix_label.endswith("_FIX") and fix_label != "NO_FIX" else fix_label


def sigma_cell(label: str, variance: float | None, zone: ThresholdZone | None, judged: float | None) -> str:
    """`label ±σ meter`; the meter judges `judged` (the worst single axis it summarizes) against `zone`.

    `variance=None` means the driver doesn't report a covariance at all:
    shown dim as "n/a", never as a reassuring green meter. An exactly-zero
    variance is the same thing in practice (no real receiver is certain to
    the millimetre; the driver just left that entry unset), so it's shown
    dim as "not reported" rather than as the best possible reading.
    """
    if variance is None:
        return f"{label} [dim]n/a (unknown)[/dim]"
    if variance == 0.0:
        return f"{label} [dim]±0 (not reported)[/dim]"
    bar = meter(judged if judged is not None else variance, zone, 6)
    return f"{label} {fmt_sigma(variance)} {bar}".rstrip()


def horizontal_vertical(covariance: tuple[float, float, float]) -> tuple[float, float]:
    """(σh², σv²) from the east/north/up variances: σh = √(σx²+σy²), σv = σz."""
    var_x, var_y, var_z = covariance
    return var_x + var_y, var_z


def sigma_h(covariance: tuple[float, float, float]) -> float:
    return math.sqrt(max(0.0, horizontal_vertical(covariance)[0]))


def fix_history(dropouts: Iterable[bool], severity: int) -> str:
    """The rolling fix window, oldest first: a green square per fix, a red hollow one per dropout, then the count."""
    window = list(dropouts)
    squares = "".join(
        colorize(EMPTY_SQUARE, Severity.ERROR) if dropped else colorize(FILLED_SQUARE, Severity.OK) for dropped in window
    )
    count = colorize(f"{sum(window)}/{len(window)} dropped", severity)
    return f"{squares}  {count}"


def services(bitmask: int) -> str:
    """The constellations a receiver reports using, e.g. "GPS+GAL", or "–" when it doesn't say."""
    names = [name for bit, name in _SERVICES if bitmask & bit]
    return "+".join(names) if names else "–"


def position_line(latitude: float, longitude: float, altitude: float) -> str:
    """lat/lon at 7 decimals (≈1 cm), dimmed: context to paste into a map, not a verdict."""
    return f"[dim]lat {latitude:.7f}  lon {longitude:.7f}  alt {altitude:.1f} m[/dim]"
