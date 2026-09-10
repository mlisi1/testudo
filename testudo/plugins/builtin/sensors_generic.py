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
from typing import Any

from sensor_msgs.msg import Imu

from testudo.plugins.base import CheckPlugin, CheckStatus, Severity, ThresholdZone, evaluate_zone

_IMU_TYPE = "sensor_msgs/msg/Imu"

#: Sanity ceilings, not a "robot is stationary at 1g" assumption -- just a
#: bound past which an IMU reading is implausible for a ground robot.
_IMU_ACCEL_MAGNITUDE_CEILING_MPS2 = 100.0
_IMU_GYRO_MAGNITUDE_CEILING_RADPS = 50.0


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

    @classmethod
    def msg_types(cls) -> tuple[str, ...]:
        return (_IMU_TYPE,)

    @classmethod
    def default_thresholds(cls) -> dict[str, ThresholdZone]:
        return {
            "invalid_ratio": ThresholdZone(green=0.01, orange=0.05),
            "stuck_count": ThresholdZone(green=2, orange=10),
        }

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

        values = {
            "invalid_ratio": f"{self._invalid_ratio:.4g}",
            "stuck_count": str(self._stuck_count),
            "frame_id": self._last_frame_id or "",
            "detail": self._detail,
        }
        return CheckStatus(severity=worst, label="sensor", message=message, values=values)
