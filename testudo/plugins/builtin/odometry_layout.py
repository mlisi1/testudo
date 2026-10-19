"""Detail Panel layout for `OdometryPlugin`: pure string builders, no state.

Split out of odometry.py so the plugin module keeps to its checks. One
row per axis, three groups side by side -- position σ, orientation σ, and
actual vs. commanded velocity -- so the cmd_vel cross-check reads as two
numbers next to each other instead of a sentence:

```
x      ±0.26m ▮▮▯▯▯▯   roll   ±0.0° ▮▮▮▮▮▮   v    0.42  cmd 0.50 m/s
y      ±0.26m ▮▮▯▯▯▯   pitch  ±0.0° ▮▮▮▮▮▮   ω    0.10  cmd 0.12 rad/s
z      ±0.0m  ▮▮▮▮▮▮   yaw    ±0.4° ▮▮▮▮▮▮   PSD  ✓ pose  ✓ twist
```
"""
from __future__ import annotations

import math

from testudo.plugins.base import Severity, ThresholdZone, colorize
from testudo.plugins.render import fmt_sigma, meter

POSITION_AXES = ("x", "y", "z")
ORIENTATION_AXES = ("roll", "pitch", "yaw")

_LABEL_WIDTH = 6
_SIGMA_WIDTH = 7
_SPEED_WIDTH = 6
_GROUP_GAP = "   "

#: Radians² -> degrees², so an orientation variance reads as a σ in degrees.
_RAD2_TO_DEG2 = (180.0 / math.pi) ** 2


def _sigma_chunk(label: str, variance: float, zone: ThresholdZone | None, segments: int, unit: str, scale: float) -> str:
    """`label  ±σ  meter` for one axis -- the meter judges the variance itself, against `zone`."""
    sigma = fmt_sigma(variance * scale, unit)
    # No zone to judge against: blank space where the meter would be, so the next group still lines up.
    bar = meter(variance, zone, segments) or " " * segments
    return f"{label:<{_LABEL_WIDTH}}{sigma:>{_SIGMA_WIDTH}} {bar}"


def _speed(value: float | None) -> str:
    return f"{value:>{_SPEED_WIDTH}.2f}" if value is not None else f"{'–':>{_SPEED_WIDTH}}"


def motion_rows(actual: tuple[float, float], commanded: tuple[float, float] | None, psd: tuple[bool, bool]) -> list[str]:
    """The third group: forward/turn speed next to the last cmd_vel (or "–"), then the PSD verdicts."""
    cmd_v, cmd_w = commanded if commanded is not None else (None, None)
    pose_ok, twist_ok = psd

    def verdict(ok: bool, name: str) -> str:
        return colorize(f"✓ {name}", Severity.OK) if ok else colorize(f"✗ {name}", Severity.ERROR)

    return [
        f"{'v':<5}{_speed(actual[0])}  [dim]cmd[/dim]{_speed(cmd_v)} [dim]m/s[/dim]",
        f"{'ω':<5}{_speed(actual[1])}  [dim]cmd[/dim]{_speed(cmd_w)} [dim]rad/s[/dim]",
        f"{'PSD':<5}{verdict(pose_ok, 'pose')}  {verdict(twist_ok, 'twist')}",
    ]


def sigma_rows(
    position_variance: tuple[float, float, float],
    orientation_variance: tuple[float, float, float],
    position_zone: ThresholdZone | None,
    orientation_zone: ThresholdZone | None,
    segments: int,
) -> list[str]:
    """One row per axis: position σ and its meter, then orientation σ (degrees) and its meter."""
    return [
        _sigma_chunk(p_axis, p_var, position_zone, segments, "m", 1.0)
        + _GROUP_GAP
        + _sigma_chunk(o_axis, o_var, orientation_zone, segments, "°", _RAD2_TO_DEG2)
        for p_axis, p_var, o_axis, o_var in zip(POSITION_AXES, position_variance, ORIENTATION_AXES, orientation_variance)
    ]


def side_by_side(left: list[str], right: list[str]) -> str:
    """`left` and `right` rows joined pairwise with the group gap, as one multi-line block."""
    return "\n".join(f"{a}{_GROUP_GAP}{b}" for a, b in zip(left, right))


def pose_line(position: tuple[float, float, float], yaw: float) -> str:
    """The pose itself, dimmed -- context, not a verdict: the numbers are only meaningful in their own frame."""
    x, y, z = position
    return f"[dim]x {x:.3f}  y {y:.3f}  z {z:.3f}  yaw {math.degrees(yaw):.1f}°[/dim]"


def twist_sigma_line(linear_variance: float, angular_variance: float) -> str:
    """The twist covariance's forward and yaw-rate σ."""
    return f"v {fmt_sigma(linear_variance, 'm/s')}  ω {fmt_sigma(angular_variance * _RAD2_TO_DEG2, '°/s')}"
