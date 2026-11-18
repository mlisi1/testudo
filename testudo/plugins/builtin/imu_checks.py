"""The IMU plugin's checks that need more than one message: pure state, fed by `ImuPlugin`.

Each returns its active problems as (code, severity, message) tuples.
"""
from __future__ import annotations

import math
import statistics
from collections import deque

from testudo.plugins.base import Severity

Finding = tuple[str, int, str]

#: How long the IMU must look still before its acceleration is judged.
REST_WINDOW_SECONDS = 2.0
_MIN_REST_SAMPLES = 5
#: Still enough: below this turn rate, with this little change in |a|.
_REST_GYRO_RADPS = 0.1
_REST_ACCEL_STDEV_MPS2 = 0.1

#: |a| at rest, whichever axis gravity lands on (mounting and slope don't change it).
_GRAVITY_MPS2 = 9.81
_GRAVITY_TOLERANCE_MPS2 = 0.5
_REMOVED_BELOW_MPS2 = 0.3
_G_UNITS_RANGE = (0.7, 1.3)

GRAVITY_INCLUDED = "included"
GRAVITY_REMOVED = "removed"
GRAVITY_IN_G_UNITS = "in g"
GRAVITY_BAD_SCALE = "bad scale"

#: How long a one-off event stays an active code.
EVENT_HOLD_SECONDS = 10.0


def classify_gravity(magnitude: float) -> str:
    """What |a| measured at rest says about this IMU's acceleration output."""
    if abs(magnitude - _GRAVITY_MPS2) <= _GRAVITY_TOLERANCE_MPS2:
        return GRAVITY_INCLUDED
    if magnitude < _REMOVED_BELOW_MPS2:
        return GRAVITY_REMOVED
    if _G_UNITS_RANGE[0] <= magnitude <= _G_UNITS_RANGE[1]:
        return GRAVITY_IN_G_UNITS
    return GRAVITY_BAD_SCALE


class GravityCheck:
    """Judges |a| whenever the IMU looks still; keeps the last verdict while it moves."""

    def __init__(self) -> None:
        self._window: deque[tuple[float, float, float]] = deque()
        self.mode: str | None = None
        self.magnitude: float | None = None
        self._settled: str | None = None
        self._changed: tuple[float, str] | None = None

    def observe(self, accel_magnitude: float, gyro_magnitude: float, now: float) -> None:
        """Take one sample's |a| and |ω|."""
        self._window.append((now, accel_magnitude, gyro_magnitude))
        while now - self._window[0][0] > REST_WINDOW_SECONDS:
            self._window.popleft()
        magnitude = self._magnitude_at_rest()
        if magnitude is None:
            return
        self.magnitude, self.mode = magnitude, classify_gravity(magnitude)
        if self.mode in (GRAVITY_INCLUDED, GRAVITY_REMOVED):
            if self._settled not in (None, self.mode):
                self._changed = (now, f"gravity went from {self._settled} to {self.mode}")
            self._settled = self.mode

    def _magnitude_at_rest(self) -> float | None:
        """The window's mean |a| if it spans long enough and the IMU was still throughout."""
        if len(self._window) < _MIN_REST_SAMPLES or self._window[-1][0] - self._window[0][0] < 0.9 * REST_WINDOW_SECONDS:
            return None
        accels = [accel for _time, accel, _gyro in self._window]
        if max(gyro for _time, _accel, gyro in self._window) >= _REST_GYRO_RADPS:
            return None
        return statistics.fmean(accels) if statistics.pstdev(accels) < _REST_ACCEL_STDEV_MPS2 else None

    def findings(self, now: float) -> list[Finding]:
        """IMU-005 for an |a| that can't be right, IMU-006 while a recent change of mode is held."""
        findings: list[Finding] = []
        if self.mode == GRAVITY_IN_G_UNITS:
            findings.append(("IMU-005", Severity.WARN, f"|a| at rest is {self.magnitude:.2f}: reporting in g, not m/s²?"))
        elif self.mode == GRAVITY_BAD_SCALE:
            findings.append(("IMU-005", Severity.WARN, f"|a| at rest is {self.magnitude:.2f} m/s², expected 9.81 or 0"))
        if self._changed is not None and now - self._changed[0] < EVENT_HOLD_SECONDS:
            findings.append(("IMU-006", Severity.WARN, self._changed[1]))
        return findings
