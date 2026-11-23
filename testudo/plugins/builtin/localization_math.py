"""Pure numeric helpers for `LocalizationPlugin`: covariance validity, orientation, zone sharing."""
from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np

from testudo.plugins.base import ThresholdZone

#: Eigenvalues below this (not just < 0) count as a real PSD violation --
#: allows for floating-point noise around zero.
_PSD_EPSILON = -1e-6

def _is_positive_semidefinite(matrix: np.ndarray) -> bool:
    """True if the symmetric `matrix` is finite and has no eigenvalue meaningfully below zero."""
    # eigvalsh raises on some non-finite input, and returns finite values for others.
    if not np.all(np.isfinite(matrix)):
        return False
    eigenvalues = np.linalg.eigvalsh(matrix)
    return bool(np.all(eigenvalues >= _PSD_EPSILON))


def _quaternion_to_euler(x: float, y: float, z: float, w: float) -> tuple[float, float, float]:
    """(roll, pitch, yaw) in radians for the intrinsic X-Y-Z rotation `nav_msgs/Odometry`'s
    orientation covariance is expressed against (REP 103), from a unit quaternion."""
    sinr_cosp = 2.0 * (w * x + y * z)
    cosr_cosp = 1.0 - 2.0 * (x * x + y * y)
    roll = math.atan2(sinr_cosp, cosr_cosp)

    sinp = 2.0 * (w * y - z * x)
    pitch = math.copysign(math.pi / 2.0, sinp) if abs(sinp) >= 1.0 else math.asin(sinp)

    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    yaw = math.atan2(siny_cosp, cosy_cosp)

    return roll, pitch, yaw


def _split_zone(zone: ThresholdZone | None, parts: int) -> ThresholdZone | None:
    """`zone` with every boundary divided by `parts` -- a sum's zone, shared out per summand."""
    if zone is None:
        return None
    return ThresholdZone(
        *(boundary / parts if boundary is not None else None for boundary in (zone.green, zone.orange, zone.red))
    )


def growth_rate(history: Sequence[tuple[float, float]]) -> float:
    """Change per second of (time, value) samples: their least-squares slope, 0 without a time span.

    A fitted slope rather than oldest-to-newest, so one end landing on a
    peak or a trough of a saw-tooth (an EKF's covariance between GNSS
    fixes) doesn't read as a trend.
    """
    if len(history) < 2:
        return 0.0
    times = [t for t, _ in history]
    values = [v for _, v in history]
    mean_t = sum(times) / len(times)
    mean_v = sum(values) / len(values)
    spread = sum((t - mean_t) ** 2 for t in times)
    if spread <= 0.0:
        return 0.0
    return sum((t - mean_t) * (v - mean_v) for t, v in zip(times, values)) / spread
