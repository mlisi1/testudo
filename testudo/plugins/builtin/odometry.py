"""Odometry/covariance content-check plugin.

Checks a nav_msgs/msg/Odometry stream for: covariance magnitude against
configured threshold zones, positive-semi-definiteness of the pose/twist
covariance matrices, an unbounded covariance growth-rate sanity check, and
(when a `cmd_vel` related topic is configured) a cross-check against
commanded velocity to catch a robot that isn't moving despite being told to.

The Detail Panel's per-axis breakdown (position and orientation σ, each
with a zone-colored meter, beside actual vs. commanded velocity) is laid
out by `odometry_layout.py`. The pose orientation quaternion is converted
to roll/pitch/yaw via `_quaternion_to_euler` purely for display -- it plays
no part in any of this plugin's actual checks.
"""
from __future__ import annotations

import math
from collections import deque
from typing import Any

import numpy as np

from testudo.plugins.base import (
    CheckPlugin,
    CheckStatus,
    Severity,
    ThresholdZone,
    colorize,
    evaluate_zone,
)
from testudo.plugins.builtin.odometry_layout import (
    POSITION_AXES,
    motion_rows,
    pose_line,
    side_by_side,
    sigma_rows,
    twist_sigma_line,
)
from testudo.plugins.render import downsample, meter, sparkline

#: Position (x, y, z) and orientation (roll, pitch, yaw -- rotation about
#: X/Y/Z, per REP 103) variances live at these diagonal indices in the
#: row-major 6x6 pose/twist covariance arrays nav_msgs/Odometry carries.
_POSITION_VARIANCE_INDICES = (0, 7, 14)
_ROLL_VARIANCE_INDEX = 21
_PITCH_VARIANCE_INDEX = 28
_YAW_VARIANCE_INDEX = 35
_ORIENTATION_VARIANCE_INDICES = (_ROLL_VARIANCE_INDEX, _PITCH_VARIANCE_INDEX, _YAW_VARIANCE_INDEX)
#: Forward speed and yaw rate variances in the twist covariance.
_TWIST_LINEAR_X_VARIANCE_INDEX = 0
_TWIST_ANGULAR_Z_VARIANCE_INDEX = 35

#: Eigenvalues below this (not just < 0) count as a real PSD violation --
#: allows for floating-point noise around zero.
_PSD_EPSILON = -1e-6

_GROWTH_WINDOW_SIZE = 10

_CMD_MOVING_THRESHOLD_MPS = 0.05
_CMD_MOVING_THRESHOLD_RADPS = 0.05
_ODOM_STUCK_THRESHOLD_MPS = 0.02
_ODOM_STUCK_THRESHOLD_RADPS = 0.02

#: Per-axis meter width on a wide enough Detail Panel; one square on a narrow one.
_METER_SEGMENTS = 6
_COMPACT_METER_SEGMENTS = 1

#: The horizontal-σ trend: one sample per this many seconds, this many kept.
_SIGMA_SAMPLE_SECONDS = 1.0
_SIGMA_HISTORY_SIZE = 60
#: Cells the σxy sparkline is averaged down to.
_TREND_WIDTH = 20


def _is_positive_semidefinite(matrix: np.ndarray) -> bool:
    """True if the symmetric `matrix` has no eigenvalue meaningfully below zero."""
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


class OdometryPlugin(CheckPlugin):
    """Covariance sanity, growth-rate, and (optional) cmd_vel cross-check for nav_msgs/Odometry."""

    def __init__(
        self,
        thresholds: dict[str, ThresholdZone] | None = None,
        related_topics: dict[str, str] | None = None,
    ) -> None:
        super().__init__(thresholds, related_topics)
        self._message_count = 0
        self._position_covariance_trace = 0.0
        self._yaw_variance = 0.0
        self._position: tuple[float, float, float] = (0.0, 0.0, 0.0)
        self._position_variance: tuple[float, float, float] = (0.0, 0.0, 0.0)
        self._orientation_rpy: tuple[float, float, float] = (0.0, 0.0, 0.0)
        self._orientation_variance: tuple[float, float, float] = (0.0, 0.0, 0.0)
        self._pose_psd_ok = True
        self._twist_psd_ok = True
        self._growth_history: deque[tuple[float, float]] = deque(maxlen=_GROWTH_WINDOW_SIZE)
        self._growth_rate = 0.0
        self._last_linear_x = 0.0
        self._last_angular_z = 0.0
        self._last_cmd_vel: tuple[float, float] | None = None
        self._twist_variance: tuple[float, float] = (0.0, 0.0)
        self._frames: tuple[str, str] = ("", "")
        self._now = 0.0
        self._stamp_lag: float | None = None
        self._sigma_xy_history: deque[float] = deque(maxlen=_SIGMA_HISTORY_SIZE)
        self._last_sigma_sample: float | None = None

    @classmethod
    def msg_types(cls) -> tuple[str, ...]:
        return ("nav_msgs/msg/Odometry",)

    @classmethod
    def default_thresholds(cls) -> dict[str, ThresholdZone]:
        return {
            "position_covariance_trace": ThresholdZone(green=0.01, orange=0.1),
            "yaw_variance": ThresholdZone(green=0.01, orange=0.1),
            "covariance_growth_rate": ThresholdZone(green=0.01, orange=0.1),
        }

    def on_tick(self, now_seconds: float) -> None:
        self._now = now_seconds
        if self._message_count == 0:
            return
        if self._last_sigma_sample is None or now_seconds - self._last_sigma_sample >= _SIGMA_SAMPLE_SECONDS:
            self._sigma_xy_history.append(self._sigma_xy())
            self._last_sigma_sample = now_seconds

    def _sigma_xy(self) -> float:
        """Horizontal position σ in meters: sqrt(var_x + var_y), the "±26 cm" people think in."""
        return math.sqrt(max(0.0, self._position_variance[0] + self._position_variance[1]))

    def on_message(self, topic: str, msg: Any) -> None:
        if topic == self.related_topics.get("cmd_vel"):
            self._last_cmd_vel = (msg.linear.x, msg.angular.z)
            return
        self._on_odometry(msg)

    def _on_odometry(self, msg: Any) -> None:
        self._message_count += 1

        pose_covariance = msg.pose.covariance
        twist_covariance = msg.twist.covariance

        self._position_variance = tuple(float(pose_covariance[i]) for i in _POSITION_VARIANCE_INDICES)
        self._orientation_variance = tuple(float(pose_covariance[i]) for i in _ORIENTATION_VARIANCE_INDICES)
        self._position_covariance_trace = float(sum(self._position_variance))
        self._yaw_variance = self._orientation_variance[2]

        position = msg.pose.pose.position
        self._position = (float(position.x), float(position.y), float(position.z))
        orientation = msg.pose.pose.orientation
        self._orientation_rpy = _quaternion_to_euler(orientation.x, orientation.y, orientation.z, orientation.w)

        pose_matrix = np.array(pose_covariance, dtype=float).reshape(6, 6)
        twist_matrix = np.array(twist_covariance, dtype=float).reshape(6, 6)
        self._pose_psd_ok = _is_positive_semidefinite(pose_matrix)
        self._twist_psd_ok = _is_positive_semidefinite(twist_matrix)

        self._twist_variance = (
            float(twist_covariance[_TWIST_LINEAR_X_VARIANCE_INDEX]),
            float(twist_covariance[_TWIST_ANGULAR_Z_VARIANCE_INDEX]),
        )
        self._frames = (msg.header.frame_id, msg.child_frame_id)

        stamp_seconds = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        # Only meaningful once a tick has set the clock and the stamp is set at all.
        self._stamp_lag = self._now - stamp_seconds if self._now > 0 and stamp_seconds > 0 else None
        self._growth_history.append((stamp_seconds, self._position_covariance_trace))
        self._growth_rate = self._compute_growth_rate()

        self._last_linear_x = msg.twist.twist.linear.x
        self._last_angular_z = msg.twist.twist.angular.z

    def _compute_growth_rate(self) -> float:
        if len(self._growth_history) < 2:
            return 0.0
        t0, v0 = self._growth_history[0]
        t1, v1 = self._growth_history[-1]
        span = t1 - t0
        if span <= 0:
            return 0.0
        return (v1 - v0) / span

    def _cmd_vel_cross_check(self) -> tuple[int, str]:
        if self._last_cmd_vel is None:
            return Severity.OK, "no cmd_vel received yet"
        cmd_linear_x, cmd_angular_z = self._last_cmd_vel
        commanding_motion = (
            abs(cmd_linear_x) > _CMD_MOVING_THRESHOLD_MPS or abs(cmd_angular_z) > _CMD_MOVING_THRESHOLD_RADPS
        )
        odom_moving = (
            abs(self._last_linear_x) > _ODOM_STUCK_THRESHOLD_MPS
            or abs(self._last_angular_z) > _ODOM_STUCK_THRESHOLD_RADPS
        )
        if commanding_motion and not odom_moving:
            return Severity.WARN, "cmd_vel commands motion but odometry reports no movement"
        return Severity.OK, "cmd_vel/odometry consistent"

    def get_status(self) -> CheckStatus:
        if self._message_count == 0:
            return CheckStatus(severity=Severity.OK, label="odometry", message="no odometry received yet")

        trace_severity = evaluate_zone(self._position_covariance_trace, self.thresholds.get("position_covariance_trace"))
        yaw_severity = evaluate_zone(self._yaw_variance, self.thresholds.get("yaw_variance"))
        growth_severity = evaluate_zone(abs(self._growth_rate), self.thresholds.get("covariance_growth_rate"))
        psd_severity = Severity.OK if (self._pose_psd_ok and self._twist_psd_ok) else Severity.ERROR
        cmd_vel_severity, cmd_vel_message = self._cmd_vel_cross_check()

        worst = max(trace_severity, yaw_severity, growth_severity, psd_severity, cmd_vel_severity)

        problems = []
        if psd_severity != Severity.OK:
            problems.append("pose or twist covariance is not positive semi-definite")
        if trace_severity != Severity.OK:
            problems.append(f"position covariance trace {self._position_covariance_trace:.4g} out of bounds")
        if yaw_severity != Severity.OK:
            problems.append(f"yaw variance {self._yaw_variance:.4g} out of bounds")
        if growth_severity != Severity.OK:
            problems.append(f"covariance growing at {self._growth_rate:.4g}/s")
        if cmd_vel_severity != Severity.OK:
            problems.append(cmd_vel_message)
        message = "; ".join(problems) if problems else "odometry nominal"

        codes: dict[str, str] = {}
        if trace_severity != Severity.OK:
            codes["ODOM-001"] = colorize(
                f"position covariance high (trace {self._position_covariance_trace:.3g} m²)", trace_severity
            )
        if yaw_severity != Severity.OK:
            codes["ODOM-002"] = colorize(f"yaw variance high ({self._yaw_variance:.3g} rad²)", yaw_severity)
        if growth_severity != Severity.OK:
            codes["ODOM-003"] = colorize(f"covariance growing {self._growth_rate:+.3g} m²/s", growth_severity)
        if not self._pose_psd_ok:
            codes["ODOM-004"] = colorize("pose covariance not positive semi-definite", Severity.ERROR)
        if not self._twist_psd_ok:
            codes["ODOM-005"] = colorize("twist covariance not positive semi-definite", Severity.ERROR)
        if cmd_vel_severity != Severity.OK:
            codes["ODOM-006"] = colorize("cmd_vel commands motion, odometry reports none", cmd_vel_severity)

        # Position axes are judged per axis, not against the trace (a sum of
        # three variances, so judging one axis by it reads every axis as
        # healthier than it is): an explicit `position_axis_variance` zone if
        # configured, else the trace's zone split evenly across x/y/z.
        # Orientation axes share yaw_variance's zone.
        position_zone = self.thresholds.get("position_axis_variance") or _split_zone(
            self.thresholds.get("position_covariance_trace"), len(POSITION_AXES)
        )
        orientation_zone = self.thresholds.get("yaw_variance")
        growth_zone = self.thresholds.get("covariance_growth_rate")
        psd = (self._pose_psd_ok, self._twist_psd_ok)
        motion = motion_rows((self._last_linear_x, self._last_angular_z), self._last_cmd_vel, psd)

        def sigmas(segments: int) -> list[str]:
            return sigma_rows(self._position_variance, self._orientation_variance, position_zone, orientation_zone, segments)

        frame, child = self._frames
        trend = sparkline(downsample(self._sigma_xy_history, _TREND_WIDTH), trace_severity, floor=0.0)
        values = {
            # Wide: σ and motion side by side, one row per axis. Narrow: the
            # σ rows alone with one-square meters, motion on rows of its own.
            "axes": side_by_side(sigmas(_METER_SEGMENTS), motion),
            "axes_compact": "\n".join(sigmas(_COMPACT_METER_SEGMENTS) + motion),
            "σxy": (
                f"{trend or '[dim]–[/dim]'} [dim]({len(self._sigma_xy_history)} s)[/dim]   "
                f"growth {self._growth_rate:+.3f} m²/s {meter(abs(self._growth_rate), growth_zone, _METER_SEGMENTS)}   "
                f"[dim]{frame or '?'} → {child or '?'}[/dim]"
            ),
            "pose_more": pose_line(self._position, self._orientation_rpy[2]),
            "twist_σ_more": twist_sigma_line(*self._twist_variance),
        }
        if self._stamp_lag is not None:
            values["stamp_lag_more"] = f"{self._stamp_lag * 1000:.0f} ms"

        return CheckStatus(
            severity=worst,
            label="odometry",
            message=message,
            values=values,
            topic_panel_column="σxy",
            topic_panel_value=self._sigma_xy_cell(trace_severity, growth_zone),
            codes=codes,
        )

    def _sigma_xy_cell(self, severity: int, growth_zone: ThresholdZone | None) -> str:
        """The Topic Panel cell: horizontal σ colored by the trace's zone, with a trend arrow from the growth check."""
        steady = growth_zone.green if growth_zone is not None and growth_zone.green is not None else 0.0
        arrow = "↑" if self._growth_rate > steady else "↓" if self._growth_rate < -steady else ""
        return colorize(f"±{self._sigma_xy():.2f}{arrow}", severity)
