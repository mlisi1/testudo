"""IMU content-check plugin for sensor_msgs/msg/Imu.

NaN/Inf values, a reading that's stuck (identical to the previous message),
values outside a plausible range, a frame_id that changes between messages,
and, whenever the IMU looks still, whether the length of its acceleration
makes sense (`imu_checks.GravityCheck`). No setting says what to expect: an
IMU may report gravity or have it filtered out, on any axis. Also the
orientation's norm, covariances left at zero, axes clipped at their range
limit, stamp gaps and jitter, and, given an `odom` related topic to say the
robot is standing still, gyro bias.
"""
from __future__ import annotations

import math
from collections import deque
from typing import Any

from sensor_msgs.msg import Imu

from testudo.plugins.base import ActiveCodes, CheckPlugin, CheckStatus, CodeMetric, Severity, SeverityLevels, ThresholdZone, evaluate_levels
from testudo.plugins.builtin.imu_checks import (
    Finding,
    GravityCheck,
    GyroBiasCheck,
    SaturationCheck,
    StampRegularity,
    placeholder_covariances,
    quaternion_norm_error,
)
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

#: The accel or gyro value of a stream that has only ever sent zeros there.
_NOT_IN_STREAM = "[dim]always 0: not in this stream[/dim]"


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


class ImuPlugin(CheckPlugin):
    """Stuck-value, NaN/Inf, range-plausibility, and frame_id checks for an IMU."""

    def __init__(
        self,
        thresholds: dict[str, ThresholdZone] | None = None,
        related_topics: dict[str, str] | None = None,
        *,
        levels: dict[str, SeverityLevels] | None = None,
    ) -> None:
        super().__init__(thresholds, related_topics, levels=levels)
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
        # A split stream fills only half of each message (a RealSense without
        # unite_imu_method: accel/sample and gyro/sample). A real sensor is never
        # exactly 0.0 on all three axes for long, so a half that has always been
        # is one this stream doesn't carry.
        self._accel_provided = False
        self._gyro_provided = False
        self._now = 0.0
        self._gyro_trend: deque[float] = deque(maxlen=_TREND_SIZE)
        self._last_trend_sample: float | None = None
        self._gravity = GravityCheck()
        self._saturation = SaturationCheck()
        self._gyro_bias = GyroBiasCheck()
        self._stamps = StampRegularity()
        self._quaternion_error: float | None = None
        self._placeholders: list[str] = []

    @classmethod
    def msg_types(cls) -> tuple[str, ...]:
        return (_IMU_TYPE,)

    @classmethod
    def default_thresholds(cls) -> dict[str, ThresholdZone]:
        return {
            "invalid_ratio": ThresholdZone(green=0.01, orange=0.05),
            "stuck_count": ThresholdZone(green=2, orange=10),
        }

    @classmethod
    def default_levels(cls) -> dict[str, SeverityLevels]:
        # Any NaN/Inf is an ERROR: consumers such as an EKF can't survive one.
        return {
            "invalid_ratio": SeverityLevels(error=0.0),
            "stuck_count": SeverityLevels(warn=2, error=10),
            "quaternion_norm_error": SeverityLevels(warn=0.01, error=0.1),
            "saturated_samples": SeverityLevels(warn=2),
            "gyro_bias_radps": SeverityLevels(warn=0.02),
            "stamp_gap_ratio": SeverityLevels(warn=3.0),
            "stamp_jitter_ratio": SeverityLevels(warn=0.5),
        }

    def on_tick(self, now_seconds: float) -> None:
        self._now = now_seconds
        if self._message_count == 0:
            return
        if self._last_trend_sample is None or now_seconds - self._last_trend_sample >= _TREND_SAMPLE_SECONDS:
            self._gyro_trend.append(math.sqrt(sum(v * v for v in self._gyro)))
            self._last_trend_sample = now_seconds

    def on_message(self, topic: str, msg: Any) -> None:
        if topic == self.related_topics.get("odom"):
            twist = msg.twist.twist
            self._gyro_bias.observe_odometry(math.hypot(twist.linear.x, twist.linear.y), twist.angular.z, self._now)
            return
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
        # A NaN reading is IMU-001's to report: it has no magnitude to judge.
        self._plausibility_ok = invalid > 0 or (
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

        self._accel_provided = self._accel_provided or any(fields[0:3])
        self._gyro_provided = self._gyro_provided or any(fields[3:6])

        self._check_stuck(fields)
        if invalid == 0:
            # A gyro-only stream would otherwise read as an IMU with gravity filtered out.
            if self._accel_provided:
                self._gravity.observe(accel_magnitude, gyro_magnitude, self._now)
            self._saturation.observe(fields, self._now, self.levels.get("saturated_samples"))
            self._gyro_bias.observe_gyro(self._gyro, self._now)
        self._quaternion_error = quaternion_norm_error(msg.orientation, msg.orientation_covariance)
        self._placeholders = placeholder_covariances(
            {
                "orientation": msg.orientation_covariance,
                "gyro": msg.angular_velocity_covariance,
                "accel": msg.linear_acceleration_covariance,
            }
        )
        stamp = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        self._stamps.observe(stamp, self._now, self.levels.get("stamp_gap_ratio"))

    def _findings(self) -> list[Finding]:
        """The problems the checks beyond a single reading's values currently report."""
        findings = [
            *self._gravity.findings(self._now),
            *self._saturation.findings(self._now),
            *self._gyro_bias.findings(self.levels.get("gyro_bias_radps")),
            *self._stamps.findings(self._now, self.levels.get("stamp_jitter_ratio")),
        ]
        if self._quaternion_error is not None:
            severity = evaluate_levels(self._quaternion_error, self.levels.get("quaternion_norm_error"))
            if severity != Severity.OK:
                findings.append(("IMU-007", severity, f"orientation norm off by {self._quaternion_error:.3f}"))
        if self._placeholders:
            findings.append(("IMU-008", Severity.WARN, f"{', '.join(self._placeholders)} covariance all zero"))
        return findings

    def get_status(self) -> CheckStatus:
        if self._message_count == 0:
            return CheckStatus(severity=Severity.OK, label="imu", message="no messages received yet")

        invalid_severity = evaluate_levels(self._invalid_ratio, self.levels.get("invalid_ratio"))
        stuck_severity = evaluate_levels(float(self._stuck_count), self.levels.get("stuck_count"))
        plausibility_severity = Severity.OK if self._plausibility_ok else Severity.WARN
        frame_id_severity = Severity.WARN if self._frame_id_changed else Severity.OK

        findings = self._findings()
        worst = max(
            invalid_severity, stuck_severity, plausibility_severity, frame_id_severity,
            *(severity for _code, severity, _text in findings),
        )

        problems = []
        if invalid_severity != Severity.OK:
            problems.append(f"invalid ratio {self._invalid_ratio:.2%}")
        if stuck_severity != Severity.OK:
            problems.append(f"stuck for {self._stuck_count} consecutive message(s)")
        if plausibility_severity != Severity.OK:
            problems.append("reading outside plausible range")
        if frame_id_severity != Severity.OK:
            problems.append(f"frame_id changed (now '{self._last_frame_id}')")
        problems.extend(text for _code, _severity, text in findings)
        message = "; ".join(problems) if problems else self._detail

        codes = ActiveCodes()
        if invalid_severity != Severity.OK:
            codes.add("IMU-001", f"NaN/Inf in {self._invalid_ratio:.0%} of accel/gyro fields", invalid_severity)
        if stuck_severity != Severity.OK:
            codes.add("IMU-002", f"reading identical for {self._stuck_count} messages", stuck_severity)
        if plausibility_severity != Severity.OK:
            codes.add("IMU-003", f"implausible magnitude ({self._detail})", plausibility_severity)
        if frame_id_severity != Severity.OK:
            codes.add("IMU-004", f"frame_id changed (now '{self._last_frame_id}')", frame_id_severity)
        for code, severity, text in findings:
            codes.add(code, text, severity)

        accel_magnitude = math.sqrt(sum(v * v for v in self._accel))
        gyro_trend = sparkline(downsample(self._gyro_trend, _TREND_WIDTH), floor=0.0)
        # The axis carrying gravity: a mounting/frame mistake shows up as g on the wrong axis.
        gravity_axis = max(range(3), key=lambda index: abs(self._accel[index]))
        gravity_sign = "+" if self._accel[gravity_axis] >= 0 else "-"
        covariances = " · ".join(f"{name} {state}" for name, state in self._covariances.items())
        values = {
            "accel": (
                f"{_vector(self._accel, '6.2f')}   |a| {accel_magnitude:.2f} m/s²"
                if self._accel_provided
                else _NOT_IN_STREAM
            ),
            "gyro": f"{_vector(self._gyro, '7.3f')} rad/s" if self._gyro_provided else _NOT_IN_STREAM,
            "|ω|": f"{gyro_trend or '[dim]–[/dim]'} [dim]({len(self._gyro_trend)} s)[/dim]",
            "stuck": str(self._stuck_count),
            "invalid": f"{self._invalid_ratio:.0%}",
            "covariance": covariances,
            "frame": self._last_frame_id or "",
        }
        if self._accel_provided:
            values["gravity_on_more"] = f"{gravity_sign}{_AXES[gravity_axis]}"
        if self._gyro_bias.bias is not None:
            values["gyro_bias"] = f"{self._gyro_bias.bias:.4f} rad/s"
        if self._gravity.mode is not None:
            values["gravity"] = f"{self._gravity.mode}  [dim]|a| {self._gravity.magnitude:.2f} at rest[/dim]"
        # |a| stays uncolored: it's only judged at rest (see GravityCheck).
        return CheckStatus(
            severity=worst,
            label="imu",
            message=message,
            values=values,
            topic_panel_column="|a|",
            topic_panel_value=f"{accel_magnitude:.2f}" if self._accel_provided else "[dim]–[/dim]",
            codes=codes.messages,
            code_severities=codes.severities,
            code_metrics=self._code_metrics(),
            metrics=self._metrics(accel_magnitude),
        )

    def _code_metrics(self) -> dict[str, CodeMetric]:
        """The numbers behind the IMU codes decided by a level."""
        metrics = {
            "IMU-001": CodeMetric("invalid_ratio", self._invalid_ratio),
            "IMU-002": CodeMetric("stuck_count", float(self._stuck_count)),
        }
        if self._quaternion_error is not None:
            metrics["IMU-007"] = CodeMetric("quaternion_norm_error", self._quaternion_error)
        if self._gyro_bias.bias is not None:
            metrics["IMU-010"] = CodeMetric("gyro_bias_radps", self._gyro_bias.bias)
        return metrics

    def _metrics(self, accel_magnitude: float) -> dict[str, float | str]:
        """The published numbers: accel/gyro per axis and magnitude, stuck/invalid, plausibility."""
        metrics: dict[str, float | str] = {}
        for axis, accel, gyro in zip(_AXES, self._accel, self._gyro):
            metrics[f"accel_{axis}_m_per_s2"] = accel
            metrics[f"gyro_{axis}_rad_per_s"] = gyro
        metrics.update(
            {
                "accel_magnitude_m_per_s2": accel_magnitude,
                "gyro_magnitude_rad_per_s": math.sqrt(sum(v * v for v in self._gyro)),
                "stuck_count": self._stuck_count,
                "invalid_ratio": self._invalid_ratio,
                "plausible": self._plausibility_ok,
                "frame_id": self._last_frame_id or "",
            }
        )
        optional = {
            "gyro_bias_rad_per_s": self._gyro_bias.bias,
            "quaternion_norm_error": self._quaternion_error,
            "stamp_jitter_ratio": self._stamps.jitter(),
        }
        metrics.update({key: value for key, value in optional.items() if value is not None})
        if self._gravity.magnitude is not None:
            metrics["accel_magnitude_at_rest_m_per_s2"] = self._gravity.magnitude
            metrics["gravity"] = self._gravity.mode or ""
        return metrics
