"""The IMU plugin's checks that need more than one message: pure state, fed by `ImuPlugin`.

Each returns its active problems as (code, severity, message) tuples.
"""
from __future__ import annotations

import math
import statistics
from collections import deque

from typing import Any

from testudo.plugins.base import Severity, SeverityLevels, evaluate_levels

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

#: Below these an axis repeating a value isn't clipping at its range limit:
#: no IMU's full scale is under 2 g or 2 rad/s.
_SATURATION_FLOOR_MPS2 = 19.6
_SATURATION_FLOOR_RADPS = 2.0

#: Odometry below these counts as the robot standing still.
_STILL_MPS = 0.01
_STILL_RADPS = 0.01
#: An odometry message older than this no longer says the robot is still.
_ODOM_EXPIRY_SECONDS = 1.0

#: Stamp intervals kept for the gap and jitter checks, and the fewest worth judging.
_STAMP_INTERVALS = 20
_MIN_INTERVALS_FOR_GAP = 5
_MIN_INTERVALS_FOR_JITTER = 10
#: How much older than usual (arrival time minus stamp) the message ending a
#: gap may arrive before the gap is put down to Testudo falling behind: a
#: stalled spin thread finds that message waiting in its own queue, at
#: least a queue depth of messages old (25 ms at 400 Hz), while after a
#: real dropout it arrives as fresh as any other.
_BEHIND_SECONDS = 0.01


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


def quaternion_norm_error(orientation: Any, covariance: Any) -> float | None:
    """How far the orientation's norm is from 1; None when the IMU reports no orientation.

    REP 145's -1 sentinel says so explicitly; an all-zero quaternion with an
    all-zero covariance is a driver that never filled the field in (which
    `placeholder_covariances` reports).
    """
    quaternion = (orientation.x, orientation.y, orientation.z, orientation.w)
    if covariance[0] == -1 or (not any(quaternion) and not any(covariance)):
        return None
    return abs(math.sqrt(sum(value * value for value in quaternion)) - 1.0)


def placeholder_covariances(covariances: dict[str, Any]) -> list[str]:
    """The named 3x3 covariance blocks left all zero, which claims a perfect sensor."""
    return [name for name, covariance in covariances.items() if not any(covariance)]


class SaturationCheck:
    """An axis repeating the same large value: clipped at the sensor's range limit (IMU-009)."""

    def __init__(self) -> None:
        self._last: tuple[float, ...] | None = None
        self._runs = [0] * 6
        self._held: tuple[float, int, str] | None = None

    def observe(self, fields: tuple[float, ...], now: float, levels: SeverityLevels | None) -> None:
        """Take one sample's (accel x, y, z, gyro x, y, z). All six repeating is IMU-002's frozen driver, not clipping."""
        previous, self._last = self._last, fields
        if previous is None or previous == fields:
            self._runs = [0] * 6
            return
        for index, (value, last) in enumerate(zip(fields, previous)):
            floor = _SATURATION_FLOOR_MPS2 if index < 3 else _SATURATION_FLOOR_RADPS
            self._runs[index] = self._runs[index] + 1 if value == last and abs(value) >= floor else 0
        worst = max(range(6), key=self._runs.__getitem__)
        severity = evaluate_levels(self._runs[worst], levels)
        if severity != Severity.OK:
            sensor = "accel" if worst < 3 else "gyro"
            axis = "xyz"[worst % 3]
            self._held = (now, severity, f"{sensor} {axis} clipped at {fields[worst]:.3g} for {self._runs[worst]} samples")

    def findings(self, now: float) -> list[Finding]:
        """IMU-009 while recent clipping is held."""
        if self._held is None or now - self._held[0] >= EVENT_HOLD_SECONDS:
            return []
        return [("IMU-009", self._held[1], self._held[2])]


class GyroBiasCheck:
    """The gyro's mean reading while odometry says the robot is standing still.

    Needs that second opinion: a steady reading alone can't tell a biased
    gyro from a robot turning at a constant rate.
    """

    def __init__(self) -> None:
        self._window: deque[tuple[float, tuple[float, float, float]]] = deque()
        self._still_since: float | None = None
        self._last_odom: float | None = None
        self.bias: float | None = None

    def observe_odometry(self, linear: float, angular: float, now: float) -> None:
        """Take one odometry message's speeds."""
        expired = self._last_odom is None or now - self._last_odom > _ODOM_EXPIRY_SECONDS
        self._last_odom = now
        if abs(linear) >= _STILL_MPS or abs(angular) >= _STILL_RADPS:
            self._still_since = None
        elif self._still_since is None or expired:
            self._still_since = now

    def observe_gyro(self, gyro: tuple[float, float, float], now: float) -> None:
        """Take one gyro sample; refreshes `bias` once the robot has been still for a full window."""
        self._window.append((now, gyro))
        while now - self._window[0][0] > REST_WINDOW_SECONDS:
            self._window.popleft()
        if not self._robot_still(now) or len(self._window) < _MIN_REST_SAMPLES:
            return
        means = [statistics.fmean(sample[axis] for _time, sample in self._window) for axis in range(3)]
        self.bias = math.sqrt(sum(mean * mean for mean in means))

    def _robot_still(self, now: float) -> bool:
        if self._still_since is None or self._last_odom is None or now - self._last_odom > _ODOM_EXPIRY_SECONDS:
            return False
        return now - self._still_since >= REST_WINDOW_SECONDS

    def findings(self, levels: SeverityLevels | None) -> list[Finding]:
        """IMU-010 for the last bias measured."""
        if self.bias is None:
            return []
        severity = evaluate_levels(self.bias, levels)
        if severity == Severity.OK:
            return []
        return [("IMU-010", severity, f"gyro reads {self.bias:.3f} rad/s with the robot standing still")]


class StampRegularity:
    """Gaps and jitter between the stamps of the messages the plugin gets to check (IMU-011).

    High-rate topics are decimated, so this sees dropouts longer than the
    check interval, not the odd late message between two checks. A gap
    whose closing message arrived later than usual after its stamp is
    Testudo's own: its spin thread was held up and found the message
    queued, so the gap is not judged.
    """

    def __init__(self) -> None:
        self._last: float | None = None
        self._intervals: deque[float] = deque(maxlen=_STAMP_INTERVALS)
        self._lags: deque[float] = deque(maxlen=_STAMP_INTERVALS)
        self._held: tuple[float, int, str] | None = None

    def observe(self, stamp: float, now: float, gap_levels: SeverityLevels | None) -> None:
        """Take one message's stamp, `now` being when it arrived; an unset stamp is the core stamp check's to report."""
        if stamp <= 0:
            return
        previous, self._last = self._last, stamp
        if previous is None or stamp == previous:
            return
        interval = stamp - previous
        if interval < 0:
            self._held = (now, Severity.WARN, f"stamp went back {-interval:.3f} s")
            return
        lag = now - stamp
        if len(self._intervals) >= _MIN_INTERVALS_FOR_GAP:
            usual = statistics.median(self._intervals)
            severity = evaluate_levels(interval / usual, gap_levels)
            if severity != Severity.OK:
                if lag - statistics.median(self._lags) <= _BEHIND_SECONDS:
                    self._held = (now, severity, f"{interval:.3f} s between stamps, usually {usual:.3f} s")
                return  # a dropout says nothing about the usual interval, or the usual lag
        self._intervals.append(interval)
        self._lags.append(lag)

    def jitter(self) -> float | None:
        """The stamp intervals' standard deviation over their mean, once there are enough of them."""
        if len(self._intervals) < _MIN_INTERVALS_FOR_JITTER:
            return None
        return statistics.pstdev(self._intervals) / statistics.fmean(self._intervals)

    def findings(self, now: float, jitter_levels: SeverityLevels | None) -> list[Finding]:
        """IMU-011 for a held gap or backward step, else for sustained jitter."""
        if self._held is not None and now - self._held[0] < EVENT_HOLD_SECONDS:
            return [("IMU-011", self._held[1], self._held[2])]
        jitter = self.jitter()
        severity = Severity.OK if jitter is None else evaluate_levels(jitter, jitter_levels)
        return [] if severity == Severity.OK else [("IMU-011", severity, f"stamp jitter {jitter:.0%} of the interval")]
