"""Odometry/covariance content-check plugin.

Checks a nav_msgs/msg/Odometry stream for: covariance magnitude against
configured threshold zones, positive-semi-definiteness of the pose/twist
covariance matrices, an unbounded covariance growth-rate sanity check, and
(when a `cmd_vel` related topic is configured) a cross-check against
commanded velocity to catch a robot that isn't moving despite being told to.

The Detail Panel's per-axis breakdown (position x/y/z, orientation
roll/pitch/yaw, each with its own covariance and a zone-colored meter)
converts the pose orientation quaternion to roll/pitch/yaw via
`_quaternion_to_euler` purely for display, matching the axes
`nav_msgs/Odometry`'s orientation covariance is already expressed against
(REP 103) -- it plays no part in any of this plugin's actual checks.
"""
from __future__ import annotations

import math
from collections import deque
from typing import Any

import numpy as np

from testudo.plugins.base import (
    SEVERITY_COLORS,
    CheckPlugin,
    CheckStatus,
    Severity,
    ThresholdZone,
    colorize,
    evaluate_zone,
)

#: Position (x, y, z) and orientation (roll, pitch, yaw -- rotation about
#: X/Y/Z, per REP 103) variances live at these diagonal indices in the
#: row-major 6x6 pose/twist covariance arrays nav_msgs/Odometry carries.
_POSITION_VARIANCE_INDICES = (0, 7, 14)
_ROLL_VARIANCE_INDEX = 21
_PITCH_VARIANCE_INDEX = 28
_YAW_VARIANCE_INDEX = 35
_ORIENTATION_VARIANCE_INDICES = (_ROLL_VARIANCE_INDEX, _PITCH_VARIANCE_INDEX, _YAW_VARIANCE_INDEX)

_POSITION_AXES = ("x", "y", "z")
_ORIENTATION_AXES = ("roll", "pitch", "yaw")

#: Eigenvalues below this (not just < 0) count as a real PSD violation --
#: allows for floating-point noise around zero.
_PSD_EPSILON = -1e-6

_GROWTH_WINDOW_SIZE = 10

_CMD_MOVING_THRESHOLD_MPS = 0.05
_CMD_MOVING_THRESHOLD_RADPS = 0.05
_ODOM_STUCK_THRESHOLD_MPS = 0.02
_ODOM_STUCK_THRESHOLD_RADPS = 0.02

#: When a metric's threshold zone doesn't configure `red` (none of the
#: built-in defaults do), a meter's "empty" reference is `orange` scaled by
#: this factor instead.
_METER_FALLBACK_SCALE = 2.0

#: Per-axis meter width (filled/empty squares) on a wide enough Detail
#: Panel; collapses to one square (see `_meter`) on a narrow one. Kept
#: short (not e.g. 10+) since position and orientation sit side by side
#: (see `_pose_block_wide`), each getting roughly half the Detail Panel's
#: width, not all of it.
_METER_SEGMENTS = 6
_COMPACT_METER_SEGMENTS = 1

_EMPTY_SQUARE = "▯"
_FILLED_SQUARE = "▮"

_AXIS_LABEL_WIDTH = 6
_AXIS_VALUE_WIDTH = 7
_AXIS_VARIANCE_WIDTH = 7
#: Gap between the position (left) and orientation (right) columns in the
#: wide side-by-side layout.
_GROUP_GAP = "   "


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


def _meter(value: float, zone: ThresholdZone | None, segments: int) -> str:
    """A `segments`-wide health-bar-style meter for `value` against `zone`.

    Full and green means good; as `value` gets worse the bar drains and
    turns orange, then red -- the fill level *and* its one color both track
    the same thing (how much margin is left), rather than a per-segment
    gradient. Never drains to fully empty (`value` far past `zone.orange`/
    `zone.red` still leaves one square filled) so a bad reading is legible
    as "almost empty and red", not indistinguishable from no data.
    `segments=1` collapses this to that one square, colored the same way:
    the narrow-Detail-Panel fallback per TUI_DATA_DESIGN.md's meter proposal.
    """
    if zone is None or zone.orange is None:
        return ""
    color = SEVERITY_COLORS.get(evaluate_zone(value, zone), "white")
    if segments <= 1:
        return f"[{color}]{_FILLED_SQUARE}[/{color}]"
    span = zone.red if zone.red is not None else zone.orange * _METER_FALLBACK_SCALE
    remaining = 1.0 - value / span if span > 0 else 0.0
    filled = max(1, min(segments, round(remaining * segments)))
    return f"[{color}]{_FILLED_SQUARE * filled}[/{color}]{_EMPTY_SQUARE * (segments - filled)}"


def _axis_line(label: str, value: float, variance: float, zone: ThresholdZone | None, segments: int) -> str:
    """One line: an axis's value and its covariance, with a meter for the latter.

    Column widths match `_axis_group_header` so a header row lines up with
    every axis row under it.
    """
    meter = _meter(variance, zone, segments)
    meter_part = f" {meter}" if meter else ""
    return (
        f"{label:<{_AXIS_LABEL_WIDTH}}{value:>{_AXIS_VALUE_WIDTH}.3f} "
        f"{variance:>{_AXIS_VARIANCE_WIDTH}.3f}{meter_part}"
    )


def _axis_group_header(segments: int) -> str:
    """Bold "value"/"cov" column headers aligned over `_axis_line`'s numeric columns.

    Takes `segments` to pad out a trailing blank exactly as wide as
    `_axis_line`'s own meter column (`" " + segments` chars, matching
    `meter_part` there) -- without it, a *second* header (e.g. orientation's,
    to the right of position's) starts too early, before where its axis
    rows actually begin.
    """
    meter_width = 1 + segments
    return (
        f"{'':<{_AXIS_LABEL_WIDTH}}[b]{'value':>{_AXIS_VALUE_WIDTH}}[/b] "
        f"[b]{'cov':>{_AXIS_VARIANCE_WIDTH}}[/b]{'':<{meter_width}}"
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

        # TUI_DATA_DESIGN.md's error-code proposal: every currently active
        # problem, independently of one another (unlike `message`, which
        # rolls them all into one string) -- a topic can have several active
        # codes at once (e.g. ODOM-001 and ODOM-006 simultaneously). Kept to
        # a couple words each: these render inline next to the roll/pitch/
        # yaw rows (see `_pose_block_wide`), not as their own section, so
        # there's no room for a full sentence per code.
        codes: dict[str, str] = {}
        if trace_severity != Severity.OK:
            codes["ODOM-001"] = colorize("cov high", trace_severity)
        if yaw_severity != Severity.OK:
            codes["ODOM-002"] = colorize("yaw high", yaw_severity)
        if growth_severity != Severity.OK:
            codes["ODOM-003"] = colorize("growing", growth_severity)
        if not self._pose_psd_ok:
            codes["ODOM-004"] = colorize("pose PSD", Severity.ERROR)
        if not self._twist_psd_ok:
            codes["ODOM-005"] = colorize("twist PSD", Severity.ERROR)
        if cmd_vel_severity != Severity.OK:
            codes["ODOM-006"] = colorize("cmd_vel", cmd_vel_severity)

        cmd_vel_topic = self.related_topics.get("cmd_vel")
        cmd_vel_detail = f"cmd_vel ({cmd_vel_topic}): {cmd_vel_message}" if cmd_vel_topic else f"cmd_vel: {cmd_vel_message}"

        # Per-axis position/orientation breakdown (values + their own
        # covariance, one meter each) per TUI_DATA_DESIGN.md's Odometry
        # Detail Panel proposal -- supersedes a flat position_covariance_trace/
        # yaw_variance pair with the individual x/y/z and roll/pitch/yaw
        # numbers those two were only ever a summary of. Position axes share
        # position_covariance_trace's threshold zone, orientation axes share
        # yaw_variance's -- reusing the two existing configurable metrics
        # rather than needing six new per-axis config keys.
        #
        # Wide layout: position and orientation side by side (3 rows + a
        # "value"/"var" column-header row) -- there's usually horizontal
        # room going spare that a single stacked column never used. Compact
        # (narrow-terminal) layout stays a plain 6-row stack with 1-segment
        # meters: a real second column needs real width, which is exactly
        # what a narrow panel doesn't have.
        position_zone = self.thresholds.get("position_covariance_trace")
        orientation_zone = self.thresholds.get("yaw_variance")
        position_data = list(zip(_POSITION_AXES, self._position, self._position_variance))
        orientation_data = list(zip(_ORIENTATION_AXES, self._orientation_rpy, self._orientation_variance))

        # Active error codes render as short "[CODE] text" tags to the right
        # of the roll/pitch/yaw rows -- one per row, in the same order as
        # `codes` (ODOM-001 first) -- rather than their own section, so
        # they're visible without costing any extra lines. A topic with more
        # than 3 simultaneously-active codes (all six is possible) overflows
        # onto extra lines below the 3 rows instead of being dropped.
        error_tags = [f"[b]\\[{code}][/b] {short_message}" for code, short_message in codes.items()]

        def _pose_block_wide() -> str:
            header = _axis_group_header(_METER_SEGMENTS) + _GROUP_GAP + _axis_group_header(_METER_SEGMENTS)
            rows = [header]
            for index, ((p_label, p_value, p_var), (o_label, o_value, o_var)) in enumerate(
                zip(position_data, orientation_data)
            ):
                left = _axis_line(p_label, p_value, p_var, position_zone, _METER_SEGMENTS)
                right = _axis_line(o_label, o_value, o_var, orientation_zone, _METER_SEGMENTS)
                row = f"{left}{_GROUP_GAP}{right}"
                if index < len(error_tags):
                    row = f"{row}{_GROUP_GAP}{error_tags[index]}"
                rows.append(row)
            rows.extend(error_tags[len(position_data) :])
            # Trailing "\n": a little breathing room before the growth/PSD/
            # cmd_vel line that follows this one in `values` -- the generic
            # Detail Panel renderer joins consecutive `values` entries with
            # one "\n" of its own, so this trailing one plus that join
            # together leave exactly one blank line between the two.
            return "\n".join(rows) + "\n"

        def _pose_block_compact() -> str:
            lines = (
                [
                    _axis_line(label, value, var, position_zone, _COMPACT_METER_SEGMENTS)
                    for label, value, var in position_data
                ]
                + [
                    _axis_line(label, value, var, orientation_zone, _COMPACT_METER_SEGMENTS)
                    for label, value, var in orientation_data
                ]
                + error_tags
            )
            return "\n".join(lines) + "\n"

        pose_psd = f"pose{'✓' if self._pose_psd_ok else '✗'}"
        twist_psd = f"twist{'✓' if self._twist_psd_ok else '✗'}"
        growth_meter = _meter(abs(self._growth_rate), self.thresholds.get("covariance_growth_rate"), _METER_SEGMENTS)
        growth_meter_compact = _meter(
            abs(self._growth_rate), self.thresholds.get("covariance_growth_rate"), _COMPACT_METER_SEGMENTS
        )

        # growth-rate + PSD + cmd_vel cross-check share one line -- put the
        # cmd_vel result to the right of growth/PSD rather than its own row,
        # since there's horizontal room going spare here just like there was
        # for the position/orientation blocks above.
        values = {
            "position_orientation": _pose_block_wide(),
            "position_orientation_compact": _pose_block_compact(),
            "growth_psd": f"{abs(self._growth_rate):>7.3f}/s {growth_meter}  PSD {pose_psd} {twist_psd}   {cmd_vel_detail}",
            "growth_psd_compact": (
                f"{abs(self._growth_rate):>7.3f}/s {growth_meter_compact}  PSD {pose_psd} {twist_psd}   {cmd_vel_detail}"
            ),
        }

        # Topic Panel "Cov" column per TUI_DATA_DESIGN.md's Odometry proposal
        # (Option 1): position_covariance_trace, zone-colored -- the single
        # most continuously informative number for "is localization
        # confidence degrading", scanned across many odometry topics at once.
        trace_color = SEVERITY_COLORS.get(trace_severity, "white")
        topic_panel_value = f"[{trace_color}]{self._position_covariance_trace:.3f}[/{trace_color}]"

        # `codes` is deliberately NOT also passed to CheckStatus here: it's
        # already rendered inline (see `error_tags` above), and the generic
        # Detail Panel would otherwise show every active code a second time
        # in its own fallback section below `values`. That fallback stays
        # for plugins/liveness statuses that don't have a custom placement
        # for their codes the way this one now does.
        return CheckStatus(
            severity=worst,
            label="odometry",
            message=message,
            values=values,
            topic_panel_column="Cov",
            topic_panel_value=topic_panel_value,
        )
