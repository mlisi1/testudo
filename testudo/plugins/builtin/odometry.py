"""Odometry/covariance content-check plugin.

Checks a nav_msgs/msg/Odometry stream for: covariance magnitude against
configured threshold zones, positive-semi-definiteness of the pose/twist
covariance matrices, an unbounded covariance growth-rate sanity check, and
(when a `cmd_vel` related topic is configured) a cross-check against
commanded velocity to catch a robot that isn't moving despite being told to.
"""
from __future__ import annotations

from collections import deque
from typing import Any

import numpy as np

from testudo.plugins.base import CheckPlugin, CheckStatus, Severity, ThresholdZone, evaluate_zone

#: Position (x, y, z) and yaw (rot_z) variances live at these indices in the
#: row-major 6x6 pose/twist covariance arrays nav_msgs/Odometry carries.
_POSITION_VARIANCE_INDICES = (0, 7, 14)
_YAW_VARIANCE_INDEX = 35

#: Eigenvalues below this (not just < 0) count as a real PSD violation --
#: allows for floating-point noise around zero.
_PSD_EPSILON = -1e-6

_GROWTH_WINDOW_SIZE = 10

_CMD_MOVING_THRESHOLD_MPS = 0.05
_CMD_MOVING_THRESHOLD_RADPS = 0.05
_ODOM_STUCK_THRESHOLD_MPS = 0.02
_ODOM_STUCK_THRESHOLD_RADPS = 0.02


def _is_positive_semidefinite(matrix: np.ndarray) -> bool:
    """True if the symmetric `matrix` has no eigenvalue meaningfully below zero."""
    eigenvalues = np.linalg.eigvalsh(matrix)
    return bool(np.all(eigenvalues >= _PSD_EPSILON))


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
        self._pose_psd_ok = True
        self._twist_psd_ok = True
        self._growth_history: deque[tuple[float, float]] = deque(maxlen=_GROWTH_WINDOW_SIZE)
        self._growth_rate = 0.0
        self._last_linear_x = 0.0
        self._last_angular_z = 0.0
        self._last_cmd_vel: tuple[float, float] | None = None

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

    def on_message(self, topic: str, msg: Any) -> None:
        if topic == self.related_topics.get("cmd_vel"):
            self._last_cmd_vel = (msg.linear.x, msg.angular.z)
            return
        self._on_odometry(msg)

    def _on_odometry(self, msg: Any) -> None:
        self._message_count += 1

        pose_covariance = msg.pose.covariance
        twist_covariance = msg.twist.covariance

        self._position_covariance_trace = float(sum(pose_covariance[i] for i in _POSITION_VARIANCE_INDICES))
        self._yaw_variance = float(pose_covariance[_YAW_VARIANCE_INDEX])

        pose_matrix = np.array(pose_covariance, dtype=float).reshape(6, 6)
        twist_matrix = np.array(twist_covariance, dtype=float).reshape(6, 6)
        self._pose_psd_ok = _is_positive_semidefinite(pose_matrix)
        self._twist_psd_ok = _is_positive_semidefinite(twist_matrix)

        stamp_seconds = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
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

        values = {
            "position_covariance_trace": f"{self._position_covariance_trace:.4g}",
            "yaw_variance": f"{self._yaw_variance:.4g}",
            "covariance_growth_rate": f"{self._growth_rate:.4g}",
            "pose_psd_ok": str(self._pose_psd_ok),
            "twist_psd_ok": str(self._twist_psd_ok),
            "cmd_vel_check": cmd_vel_message,
        }
        return CheckStatus(severity=worst, label="odometry", message=message, values=values)
