"""Generic sensor content-check plugin.

Covers sensor_msgs/msg/Imu with checks that generalize across simple raw
sensor shapes: NaN/Inf values, a reading that's stuck (identical to the
previous message), values outside a plausible range, and a frame_id that
changes between messages. LaserScan (and PointCloud2) live in
`point_stream.py`'s PointStreamPlugin instead -- lidar-shaped data is its
own semantic domain (2D/3D range readings), not a generic sensor shape,
and PointCloud2's presence-only-by-default tiering needs to live with a
plugin that actually implements its checks.
"""
from __future__ import annotations

import math
from collections import deque
from typing import Any

from sensor_msgs.msg import Imu

from testudo.plugins.base import CheckPlugin, CheckStatus, Severity, ThresholdZone, colorize, evaluate_zone
from testudo.plugins.render import downsample, sparkline

_IMU_TYPE = "sensor_msgs/msg/Imu"

#: Sanity ceilings, not a "robot is stationary at 1g" assumption -- just a
#: bound past which an IMU reading is implausible for a ground robot.
_IMU_ACCEL_MAGNITUDE_CEILING_MPS2 = 100.0
_IMU_GYRO_MAGNITUDE_CEILING_RADPS = 50.0

#: The |ω| trend: one sample per this many seconds, this many kept, averaged down to this many cells.
_TREND_SAMPLE_SECONDS = 1.0
_TREND_SIZE = 60
_TREND_WIDTH = 20

_AXES = ("x", "y", "z")


def _vector(values: tuple[float, float, float], fmt: str) -> str:
    return "  ".join(f"[dim]{axis}[/dim] {value:{fmt}}" for axis, value in zip(_AXES, values))


def _covariance_state(covariance: Any) -> str:
    """How a 3x3 covariance block is filled: "-1" = not provided (REP 145), all zero = "unknown" to most consumers."""
    if covariance[0] == -1:
        return "[dim]not provided[/dim]"
    if all(value == 0 for value in covariance):
        return "[dim]all zero[/dim]"
    return "✓"


def _is_bad_float(value: float) -> bool:
    return math.isnan(value) or math.isinf(value)


class GenericSensorPlugin(CheckPlugin):
    """Zero/stuck-value, NaN/Inf, range-plausibility, and frame_id checks for Imu."""

    def __init__(
        self,
        thresholds: dict[str, ThresholdZone] | None = None,
        related_topics: dict[str, str] | None = None,
    ) -> None:
        super().__init__(thresholds, related_topics)
        self._message_count = 0
        self._last_frame_id: str | None = None
        self._frame_id_changed = False
        self._last_signature: tuple[float, ...] | None = None
        self._stuck_count = 0
        self._invalid_ratio = 0.0
        self._plausibility_ok = True
        self._detail = "no messages received yet"
        self._accel: tuple[float, float, float] = (0.0, 0.0, 0.0)
        self._gyro: tuple[float, float, float] = (0.0, 0.0, 0.0)
        self._covariances = {"orient": "", "gyro": "", "accel": ""}
        self._now = 0.0
        self._stamp_lag: float | None = None
        self._gyro_trend: deque[float] = deque(maxlen=_TREND_SIZE)
        self._last_trend_sample: float | None = None

    @classmethod
    def msg_types(cls) -> tuple[str, ...]:
        return (_IMU_TYPE,)

    @classmethod
    def default_thresholds(cls) -> dict[str, ThresholdZone]:
        return {
            "invalid_ratio": ThresholdZone(green=0.01, orange=0.05),
            "stuck_count": ThresholdZone(green=2, orange=10),
        }

    def on_tick(self, now_seconds: float) -> None:
        self._now = now_seconds
        if self._message_count == 0:
            return
        if self._last_trend_sample is None or now_seconds - self._last_trend_sample >= _TREND_SAMPLE_SECONDS:
            self._gyro_trend.append(math.sqrt(sum(v * v for v in self._gyro)))
            self._last_trend_sample = now_seconds

    def on_message(self, topic: str, msg: Any) -> None:
        self._message_count += 1
        self._check_frame_id(msg.header.frame_id)
        self._on_imu(msg)

    def _check_frame_id(self, frame_id: str) -> None:
        if self._last_frame_id is not None and frame_id != self._last_frame_id:
            self._frame_id_changed = True
        self._last_frame_id = frame_id

    def _check_stuck(self, signature: tuple[float, ...]) -> None:
        if self._last_signature is not None and signature == self._last_signature:
            self._stuck_count += 1
        else:
            self._stuck_count = 0
        self._last_signature = signature

    def _on_imu(self, msg: Imu) -> None:
        fields = (
            msg.linear_acceleration.x,
            msg.linear_acceleration.y,
            msg.linear_acceleration.z,
            msg.angular_velocity.x,
            msg.angular_velocity.y,
            msg.angular_velocity.z,
        )
        invalid = sum(1 for value in fields if _is_bad_float(value))
        self._invalid_ratio = invalid / len(fields)

        accel_magnitude = math.sqrt(sum(v * v for v in fields[0:3]))
        gyro_magnitude = math.sqrt(sum(v * v for v in fields[3:6]))
        self._plausibility_ok = (
            accel_magnitude <= _IMU_ACCEL_MAGNITUDE_CEILING_MPS2
            and gyro_magnitude <= _IMU_GYRO_MAGNITUDE_CEILING_RADPS
        )
        self._detail = f"|accel|={accel_magnitude:.3g} m/s^2, |gyro|={gyro_magnitude:.3g} rad/s"
        self._accel = (fields[0], fields[1], fields[2])
        self._gyro = (fields[3], fields[4], fields[5])
        self._covariances = {
            "orient": _covariance_state(msg.orientation_covariance),
            "gyro": _covariance_state(msg.angular_velocity_covariance),
            "accel": _covariance_state(msg.linear_acceleration_covariance),
        }
        stamp_seconds = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        self._stamp_lag = self._now - stamp_seconds if self._now > 0 and stamp_seconds > 0 else None

        self._check_stuck(fields)

    def get_status(self) -> CheckStatus:
        if self._message_count == 0:
            return CheckStatus(severity=Severity.OK, label="sensor", message="no messages received yet")

        invalid_severity = evaluate_zone(self._invalid_ratio, self.thresholds.get("invalid_ratio"))
        stuck_severity = evaluate_zone(float(self._stuck_count), self.thresholds.get("stuck_count"))
        plausibility_severity = Severity.OK if self._plausibility_ok else Severity.WARN
        frame_id_severity = Severity.WARN if self._frame_id_changed else Severity.OK

        worst = max(invalid_severity, stuck_severity, plausibility_severity, frame_id_severity)

        problems = []
        if invalid_severity != Severity.OK:
            problems.append(f"invalid ratio {self._invalid_ratio:.2%}")
        if stuck_severity != Severity.OK:
            problems.append(f"stuck for {self._stuck_count} consecutive message(s)")
        if plausibility_severity != Severity.OK:
            problems.append("reading outside plausible range")
        if frame_id_severity != Severity.OK:
            problems.append(f"frame_id changed (now '{self._last_frame_id}')")
        message = "; ".join(problems) if problems else self._detail

        codes: dict[str, str] = {}
        if invalid_severity != Severity.OK:
            codes["SENS-001"] = colorize(f"NaN/Inf in {self._invalid_ratio:.0%} of accel/gyro fields", invalid_severity)
        if stuck_severity != Severity.OK:
            codes["SENS-002"] = colorize(f"reading identical for {self._stuck_count} messages", stuck_severity)
        if plausibility_severity != Severity.OK:
            codes["SENS-004"] = colorize(f"implausible magnitude ({self._detail})", plausibility_severity)
        if frame_id_severity != Severity.OK:
            codes["SENS-005"] = colorize(f"frame_id changed (now '{self._last_frame_id}')", frame_id_severity)

        accel_magnitude = math.sqrt(sum(v * v for v in self._accel))
        gyro_trend = sparkline(downsample(self._gyro_trend, _TREND_WIDTH), floor=0.0)
        # The axis carrying gravity: a mounting/frame mistake shows up as g on the wrong axis.
        gravity_axis = max(range(3), key=lambda index: abs(self._accel[index]))
        gravity_sign = "+" if self._accel[gravity_axis] >= 0 else "-"
        covariances = " · ".join(f"{name} {state}" for name, state in self._covariances.items())
        values = {
            "accel": f"{_vector(self._accel, '6.2f')}   |a| {accel_magnitude:.2f} m/s²",
            "gyro": f"{_vector(self._gyro, '7.3f')} rad/s",
            "|ω|": f"{gyro_trend or '[dim]–[/dim]'} [dim]({len(self._gyro_trend)} s)[/dim]",
            "stuck": str(self._stuck_count),
            "invalid": f"{self._invalid_ratio:.0%}",
            "covariance": covariances,
            "frame": self._last_frame_id or "",
            "gravity_on_more": f"{gravity_sign}{_AXES[gravity_axis]}",
        }
        if self._stamp_lag is not None:
            values["stamp_lag_more"] = f"{self._stamp_lag * 1000:.0f} ms"
        # |a| is the strongest single sanity number (1.00 = a g-vs-m/s² unit
        # bug, 0.00 = a dead accel) but stays uncolored: some drivers publish
        # gravity-compensated acceleration, where ~0 is correct.
        return CheckStatus(
            severity=worst,
            label="sensor",
            message=message,
            values=values,
            topic_panel_column="|a|",
            topic_panel_value=f"{accel_magnitude:.2f}",
            codes=codes,
        )
