"""Localization/covariance content-check plugin.

Checks a nav_msgs/msg/Odometry or geometry_msgs/msg/PoseWithCovarianceStamped
stream (the same checks on two wire formats; a pose simply has no twist) for:
covariance magnitude against configured threshold zones, positive-semi-definiteness of the pose/twist
covariance matrices, an unbounded covariance growth-rate sanity check, and
(when a `cmd_vel` related topic is configured) a cross-check against
commanded velocity to catch a robot that isn't moving despite being told to.

The Detail Panel's per-axis breakdown (position and orientation σ, each
with a zone-colored meter, beside actual vs. commanded velocity) is laid
out by `localization_layout.py`. The pose orientation quaternion is converted
to roll/pitch/yaw via `_quaternion_to_euler` purely for display -- it plays
no part in any of this plugin's actual checks.
"""
from __future__ import annotations

import math
from collections import deque
from typing import Any

import numpy as np

from testudo.plugins.base import (
    ActiveCodes,
    CheckPlugin,
    CheckStatus,
    Severity,
    SeverityLevels,
    ThresholdZone,
    colorize,
    evaluate_levels,
)
from testudo.plugins.builtin.localization_jumps import PoseJumpCheck
from testudo.plugins.builtin.localization_motion import CommandedMotion, commands_motion
from testudo.plugins.builtin.localization_math import _is_positive_semidefinite, _quaternion_to_euler, _split_zone, growth_rate
from testudo.plugins.builtin.localization_roles import CONTINUOUS_ROLE, GLOBAL_ROLE, ROLE_LEVELS, frame_role
from testudo.plugins.builtin.localization_layout import (
    ORIENTATION_AXES,
    POSITION_AXES,
    motion_rows,
    pose_line,
    side_by_side,
    sigma_rows,
    twist_sigma_line,
)
from testudo.plugins.render import downsample, meter, sparkline

ODOMETRY_MSG_TYPE = "nav_msgs/msg/Odometry"
POSE_MSG_TYPE = "geometry_msgs/msg/PoseWithCovarianceStamped"

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

_GROWTH_WINDOW_SIZE = 10

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


class LocalizationPlugin(CheckPlugin):
    """Covariance sanity, growth-rate, and (optional) cmd_vel cross-check for a localization output."""

    def __init__(
        self,
        thresholds: dict[str, ThresholdZone] | None = None,
        related_topics: dict[str, str] | None = None,
        *,
        levels: dict[str, SeverityLevels] | None = None,
    ) -> None:
        super().__init__(thresholds, related_topics, levels=levels)
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
        self._has_twist = False
        self._role_override: str | None = None
        self._jumps = PoseJumpCheck()
        self._commanded = CommandedMotion()
        self._last_output: float | None = None
        self._sigma_xy_history: deque[float] = deque(maxlen=_SIGMA_HISTORY_SIZE)
        self._last_sigma_sample: float | None = None

    @classmethod
    def msg_types(cls) -> tuple[str, ...]:
        return (ODOMETRY_MSG_TYPE, POSE_MSG_TYPE)

    @classmethod
    def default_thresholds(cls) -> dict[str, ThresholdZone]:
        return {
            "position_covariance_trace": ThresholdZone(green=0.01, orange=0.1),
            "yaw_variance": ThresholdZone(green=0.01, orange=0.1),
            "covariance_growth_rate": ThresholdZone(green=0.01, orange=0.1),
        }

    @classmethod
    def default_levels(cls) -> dict[str, SeverityLevels]:
        # ERROR is for broken, not degraded: a 1000 m² trace is an "unknown"
        # placeholder (9999-style), a yaw variance of 10 rad² (σ > π) means
        # no heading at all. Growth has no ERROR: a diverging filter trips the trace.
        return {
            "position_covariance_trace": SeverityLevels(warn=0.01, error=1000.0),
            "yaw_variance": SeverityLevels(warn=0.01, error=10.0),
            "covariance_growth_rate": SeverityLevels(warn=0.01),
            # Meters/radians beyond what the reported motion explains. On a
            # continuous topic any jump is a bug; a global one may relocalize.
            "pose_jump_m": SeverityLevels(warn=0.1, error=1.0),
            "pose_jump_rad": SeverityLevels(warn=0.2, error=1.0),
            "global_pose_jump_m": SeverityLevels(warn=2.0, error=10.0),
            "global_pose_jump_rad": SeverityLevels(warn=1.0),
            "relocalizations_per_min": SeverityLevels(warn=3),
        }

    def set_frame_role(self, role: str) -> None:
        """Override the role `frame_role` reads off the frame (a topic's `frame_role:` in config)."""
        self._role_override = role

    def reset(self) -> None:
        """Clear the accumulated state, keeping the configured frame role."""
        role_override = self._role_override
        super().reset()
        self._role_override = role_override

    def _role(self) -> str | None:
        """This topic's role: the configured one, else what its frame implies, None when neither says."""
        return self._role_override or frame_role(self._frames[0])

    def _levels_for(self, metric: str) -> SeverityLevels | None:
        """`metric`'s levels: anything written in config as is, else the default for this topic's role."""
        configured = self.levels.get(metric)
        role_levels = ROLE_LEVELS.get(self._role() or "", {})
        if metric in role_levels and configured == self.default_levels().get(metric):
            return role_levels[metric]
        return configured

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
            # geometry_msgs Twist, or TwistStamped with the same fields under `.twist`.
            twist = getattr(msg, "twist", msg)
            self._last_cmd_vel = (twist.linear.x, twist.angular.z)
            self._commanded.on_command(twist.linear.x, twist.angular.z, self._now)
            return
        self._last_output = self._now
        self._on_pose(msg)

    def silence_expected(self, msg_type: str) -> bool:
        """A pose estimate may stay quiet at rest (AMCL only updates on motion), not while told to move.

        Odometry streams at a fixed rate in any frame, so its silence is
        always a fault; so is a pose stream in a continuous frame.
        """
        if msg_type != POSE_MSG_TYPE or self._role() == CONTINUOUS_ROLE:
            return False
        return not self._commanded.silent_too_long(self._last_output, self._now)

    def _on_pose(self, msg: Any) -> None:
        self._message_count += 1

        pose_covariance = msg.pose.covariance

        self._position_variance = tuple(float(pose_covariance[i]) for i in _POSITION_VARIANCE_INDICES)
        self._orientation_variance = tuple(float(pose_covariance[i]) for i in _ORIENTATION_VARIANCE_INDICES)
        self._position_covariance_trace = float(sum(self._position_variance))
        self._yaw_variance = self._orientation_variance[2]

        position = msg.pose.pose.position
        self._position = (float(position.x), float(position.y), float(position.z))
        orientation = msg.pose.pose.orientation
        self._orientation_rpy = _quaternion_to_euler(orientation.x, orientation.y, orientation.z, orientation.w)

        pose_matrix = np.array(pose_covariance, dtype=float).reshape(6, 6)
        self._pose_psd_ok = _is_positive_semidefinite(pose_matrix)
        if msg.header.frame_id != self._frames[0]:
            self._jumps.restart()
        self._frames = (msg.header.frame_id, getattr(msg, "child_frame_id", ""))

        stamp_seconds = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        self._jumps.observe(msg, stamp_seconds, self._now, self._role(), self.levels)
        self._growth_history.append((stamp_seconds, self._position_covariance_trace))
        self._growth_rate = growth_rate(self._growth_history)

        self._has_twist = hasattr(msg, "twist")
        if self._has_twist:
            self._on_twist(msg.twist)

    def _on_twist(self, twist: Any) -> None:
        """An Odometry message's velocity half; a pose message has none."""
        twist_covariance = twist.covariance
        self._twist_psd_ok = _is_positive_semidefinite(np.array(twist_covariance, dtype=float).reshape(6, 6))
        self._twist_variance = (
            float(twist_covariance[_TWIST_LINEAR_X_VARIANCE_INDEX]),
            float(twist_covariance[_TWIST_ANGULAR_Z_VARIANCE_INDEX]),
        )
        self._last_linear_x = twist.twist.linear.x
        self._last_angular_z = twist.twist.angular.z

    def _cmd_vel_cross_check(self) -> tuple[int, str]:
        if not self._has_twist or self._role() == GLOBAL_ROLE:
            return Severity.OK, "no velocity to cross-check on this topic"
        if self._last_cmd_vel is None:
            return Severity.OK, "no cmd_vel received yet"
        cmd_linear_x, cmd_angular_z = self._last_cmd_vel
        commanding_motion = commands_motion(cmd_linear_x, cmd_angular_z)
        odom_moving = (
            abs(self._last_linear_x) > _ODOM_STUCK_THRESHOLD_MPS
            or abs(self._last_angular_z) > _ODOM_STUCK_THRESHOLD_RADPS
        )
        if commanding_motion and not odom_moving:
            return Severity.WARN, "cmd_vel commands motion but odometry reports no movement"
        return Severity.OK, "cmd_vel/odometry consistent"

    def get_status(self) -> CheckStatus:
        if self._message_count == 0:
            return CheckStatus(severity=Severity.OK, label="localization", message="no localization data received yet")

        trace_severity = evaluate_levels(self._position_covariance_trace, self._levels_for("position_covariance_trace"))
        yaw_severity = evaluate_levels(self._yaw_variance, self._levels_for("yaw_variance"))
        growth_severity = evaluate_levels(abs(self._growth_rate), self._levels_for("covariance_growth_rate"))
        psd_severity = Severity.OK if (self._pose_psd_ok and self._twist_psd_ok) else Severity.ERROR
        cmd_vel_severity, cmd_vel_message = self._cmd_vel_cross_check()
        jumps = self._jumps.findings(self._now, self.levels)

        worst = max(
            trace_severity, yaw_severity, growth_severity, psd_severity, cmd_vel_severity,
            *(severity for _code, severity, _text in jumps),
        )

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
        problems.extend(text for _code, _severity, text in jumps)
        message = "; ".join(problems) if problems else "localization nominal"

        codes = ActiveCodes()
        if trace_severity != Severity.OK:
            codes.add(
                "LOC-001",
                f"position covariance high (trace {self._position_covariance_trace:.3g} m²)",
                trace_severity,
            )
        if yaw_severity != Severity.OK:
            codes.add("LOC-002", f"yaw variance high ({self._yaw_variance:.3g} rad²)", yaw_severity)
        if growth_severity != Severity.OK:
            codes.add("LOC-003", f"covariance growing {self._growth_rate:+.3g} m²/s", growth_severity)
        if not self._pose_psd_ok:
            codes.add("LOC-004", "pose covariance not positive semi-definite", Severity.ERROR)
        if not self._twist_psd_ok:
            codes.add("LOC-005", "twist covariance not positive semi-definite", Severity.ERROR)
        if cmd_vel_severity != Severity.OK:
            codes.add("LOC-006", "cmd_vel commands motion, odometry reports none", cmd_vel_severity)
        for code, severity, text in jumps:
            codes.add(code, text, severity)

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
        actual = (self._last_linear_x, self._last_angular_z) if self._has_twist else (None, None)
        motion = motion_rows(actual, self._last_cmd_vel, psd)

        def sigmas(segments: int) -> list[str]:
            return sigma_rows(self._position_variance, self._orientation_variance, position_zone, orientation_zone, segments)

        frame, child = self._frames
        role = self._role()
        trend = sparkline(downsample(self._sigma_xy_history, _TREND_WIDTH), trace_severity, floor=0.0)
        values = {
            # Wide: σ and motion side by side, one row per axis. Narrow: the
            # σ rows alone with one-square meters, motion on rows of its own.
            "axes": side_by_side(sigmas(_METER_SEGMENTS), motion),
            "axes_compact": "\n".join(sigmas(_COMPACT_METER_SEGMENTS) + motion),
            "σxy": (
                f"{trend or '[dim]–[/dim]'} [dim]({len(self._sigma_xy_history)} s)[/dim]   "
                f"growth {self._growth_rate:+.3f} m²/s {meter(abs(self._growth_rate), growth_zone, _METER_SEGMENTS)}   "
                f"[dim]{frame or '?'} → {child or '?'}{f' · {role}' if role else ''}[/dim]"
            ),
            "pose_more": pose_line(self._position, self._orientation_rpy[2]),
        }
        if self._has_twist:
            values["twist_σ_more"] = twist_sigma_line(*self._twist_variance)
        jump_summary = self._jumps.summary(self._now)
        if jump_summary is not None:
            values["pose_jumps"] = jump_summary

        return CheckStatus(
            severity=worst,
            label="localization",
            message=message,
            values=values,
            topic_panel_column="σxy",
            topic_panel_value=self._sigma_xy_cell(trace_severity, growth_zone),
            codes=codes.messages,
            code_severities=codes.severities,
            metrics=self._metrics(),
        )

    def _metrics(self) -> dict[str, float | str]:
        """The published numbers: pose, per-axis variances, covariance trace and growth, speeds vs cmd_vel, PSD flags."""
        frame, child = self._frames
        metrics: dict[str, float | str] = {}
        for axis, position, variance in zip(POSITION_AXES, self._position, self._position_variance):
            metrics[f"position_{axis}_m"] = position
            metrics[f"position_variance_{axis}_m2"] = variance
        for axis, angle, variance in zip(ORIENTATION_AXES, self._orientation_rpy, self._orientation_variance):
            metrics[f"{axis}_rad"] = angle
            metrics[f"{axis}_variance_rad2"] = variance
        metrics.update(
            {
                "position_covariance_trace_m2": self._position_covariance_trace,
                "sigma_xy_m": self._sigma_xy(),
                "covariance_growth_rate_m2_per_s": self._growth_rate,
                "pose_psd_ok": self._pose_psd_ok,
                "frame_id": frame,
                "child_frame_id": child,
                "frame_role": self._role() or "unknown",
                **self._jumps.metrics(self._now),
            }
        )
        if self._has_twist:
            metrics.update(
                {
                    "linear_x_m_per_s": self._last_linear_x,
                    "angular_z_rad_per_s": self._last_angular_z,
                    "linear_variance_m2_per_s2": self._twist_variance[0],
                    "angular_variance_rad2_per_s2": self._twist_variance[1],
                    "twist_psd_ok": self._twist_psd_ok,
                }
            )
        if self._last_cmd_vel is not None:
            metrics["cmd_linear_x_m_per_s"], metrics["cmd_angular_z_rad_per_s"] = self._last_cmd_vel
        return metrics

    def _sigma_xy_cell(self, severity: int, growth_zone: ThresholdZone | None) -> str:
        """The Topic Panel cell: horizontal σ colored by the trace's zone, with a trend arrow from the growth check."""
        steady = growth_zone.green if growth_zone is not None and growth_zone.green is not None else 0.0
        arrow = "↑" if self._growth_rate > steady else "↓" if self._growth_rate < -steady else ""
        return colorize(f"±{self._sigma_xy():.2f}{arrow}", severity)
