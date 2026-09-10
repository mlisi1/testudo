"""GNSS fix content-check plugin.

Covers sensor_msgs/msg/NavSatFix: fix-status sanity (a hard NO_FIX is
flagged immediately, an intermittent dropout ratio over a rolling window
catches flaky fixes under bridges/canopy that never individually last long
enough to look stale), a per-axis (x/y/z, i.e. east/north/up) position-
covariance zone check -- shown and evaluated per axis rather than summed
into one trace, so a bad vertical fix doesn't hide behind two good
horizontal ones, and skipped entirely when the driver reports
COVARIANCE_TYPE_UNKNOWN rather than treating an all-zero, unpopulated
covariance as "perfect" -- common on real hardware, and exactly the kind of
false positive this plugin should not manufacture -- plus NaN/Inf
lat/lon/altitude, a stuck (byte-identical) fix, an implausible position-jump
speed between consecutive real fixes, and frame_id consistency.

The default covariance zone is RTK-grade (green=1e-4 m^2, ~1cm std): a
consumer/SBAS-grade receiver reporting a few meters of std should override
`position_covariance` per topic in config, the same way a noisier lidar
overrides GenericSensorPlugin's `invalid_ratio`.
"""
from __future__ import annotations

import math
from collections import deque
from typing import Any

from sensor_msgs.msg import NavSatFix, NavSatStatus

from testudo.plugins.base import (
    SEVERITY_COLORS,
    CheckPlugin,
    CheckStatus,
    Severity,
    ThresholdZone,
    evaluate_zone,
)

_NAVSATFIX_TYPE = "sensor_msgs/msg/NavSatFix"

#: position_covariance is a row-major 3x3 matrix (ENU x/y/z); these are its
#: diagonal (variance) entries, in x/y/z order.
_POSITION_COVARIANCE_DIAG_INDICES = (0, 4, 8)
_POSITION_AXES = ("x", "y", "z")

_EARTH_RADIUS_M = 6371000.0

#: Per-axis meter width on a wide-enough Detail Panel; collapses to one
#: square (see `_meter`) on a narrow one -- same scheme as OdometryPlugin's
#: Detail Panel meters, duplicated here rather than shared (per the
#: project's "build concrete before abstract": this is only the second
#: plugin using it, not the third that would justify extracting it).
_METER_SEGMENTS = 6
_COMPACT_METER_SEGMENTS = 1
_EMPTY_SQUARE = "▯"
_FILLED_SQUARE = "▮"
#: When the configured zone doesn't set `red`, a meter's "empty" reference
#: is `orange` scaled by this factor instead.
_METER_FALLBACK_SCALE = 2.0
#: Gap between the x/y/z chunks when they sit side by side on one row.
_AXIS_GAP = "   "
_AXIS_VALUE_WIDTH = 9

_FIX_DROPOUT_WINDOW_SIZE = 20

_FIX_STATUS_LABELS = {
    NavSatStatus.STATUS_NO_FIX: "NO_FIX",
    NavSatStatus.STATUS_FIX: "FIX",
    NavSatStatus.STATUS_SBAS_FIX: "SBAS_FIX",
    NavSatStatus.STATUS_GBAS_FIX: "GBAS_FIX",
}

_COVARIANCE_TYPE_LABELS = {
    NavSatFix.COVARIANCE_TYPE_UNKNOWN: "unknown",
    NavSatFix.COVARIANCE_TYPE_APPROXIMATED: "approximated",
    NavSatFix.COVARIANCE_TYPE_DIAGONAL_KNOWN: "diagonal_known",
    NavSatFix.COVARIANCE_TYPE_KNOWN: "known",
}


def _is_bad_float(value: float) -> bool:
    return math.isnan(value) or math.isinf(value)


def _haversine_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters between two lat/lon points (ignores altitude)."""
    lat1_rad, lat2_rad = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2.0) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return _EARTH_RADIUS_M * c


def _meter(value: float, zone: ThresholdZone | None, segments: int) -> str:
    """A `segments`-wide health-bar-style meter for `value` against `zone` (see OdometryPlugin's `_meter`).

    Full and green means good; the bar drains and reddens as `value` gets
    worse, never fully to empty, so a bad reading stays legible as "almost
    empty and red" rather than indistinguishable from no data.
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


def _axis_chunk(axis: str, value: float, zone: ThresholdZone | None, segments: int) -> str:
    """One axis's short label, its covariance value, and its meter -- e.g. "cov_x:   0.0001 ▮▮▮▮▮▮"."""
    meter = _meter(value, zone, segments)
    meter_part = f" {meter}" if meter else ""
    return f"cov_{axis}:{value:>{_AXIS_VALUE_WIDTH}.4g}{meter_part}"


def _covariance_block(covariance: tuple[float, float, float], zone: ThresholdZone | None, segments: int, joiner: str) -> str:
    """x/y/z covariance chunks joined side by side (`joiner=_AXIS_GAP`) or stacked (`joiner="\\n"`)."""
    chunks = [_axis_chunk(axis, value, zone, segments) for axis, value in zip(_POSITION_AXES, covariance)]
    return joiner.join(chunks) + "\n"


class GnssPlugin(CheckPlugin):
    """Fix-status, covariance, dropout-ratio, jump-speed, and frame_id checks for sensor_msgs/NavSatFix."""

    def __init__(
        self,
        thresholds: dict[str, ThresholdZone] | None = None,
        related_topics: dict[str, str] | None = None,
    ) -> None:
        super().__init__(thresholds, related_topics)
        self._message_count = 0
        self._status = NavSatStatus.STATUS_NO_FIX
        self._covariance_type = NavSatFix.COVARIANCE_TYPE_UNKNOWN
        self._position_covariance: tuple[float, float, float] = (0.0, 0.0, 0.0)
        self._invalid_ratio = 0.0
        self._last_frame_id: str | None = None
        self._frame_id_changed = False
        self._last_signature: tuple[float, float, float] | None = None
        self._stuck_count = 0
        self._last_fix: tuple[float, float, float, float] | None = None  # (stamp_s, lat, lon, alt)
        self._jump_speed_mps: float | None = None
        self._dropout_window: deque[bool] = deque(maxlen=_FIX_DROPOUT_WINDOW_SIZE)
        self._dropout_ratio = 0.0

    @classmethod
    def msg_types(cls) -> tuple[str, ...]:
        return (_NAVSATFIX_TYPE,)

    @classmethod
    def default_thresholds(cls) -> dict[str, ThresholdZone]:
        return {
            # Each of x/y/z's own position-covariance diagonal entry, m^2 --
            # applied per axis, not summed. RTK-grade: green ~1cm std,
            # orange ~10cm std (0.1 m^2, i.e. ~32cm std, is already bad for
            # an RTK-corrected fix, so it lands past orange here).
            "position_covariance": ThresholdZone(green=0.0001, orange=0.01),
            "stuck_count": ThresholdZone(green=2, orange=10),
            "invalid_ratio": ThresholdZone(green=0.01, orange=0.05),
            # Implausible speed for a ground robot between consecutive real fixes.
            "jump_speed_mps": ThresholdZone(green=15.0, orange=30.0),
            "fix_dropout_ratio": ThresholdZone(green=0.05, orange=0.2),
        }

    def on_message(self, topic: str, msg: Any) -> None:
        self._message_count += 1
        self._check_frame_id(msg.header.frame_id)

        self._status = msg.status.status
        self._covariance_type = msg.position_covariance_type
        self._position_covariance = tuple(
            float(msg.position_covariance[i]) for i in _POSITION_COVARIANCE_DIAG_INDICES
        )

        fields = (msg.latitude, msg.longitude, msg.altitude)
        invalid = sum(1 for value in fields if _is_bad_float(value))
        self._invalid_ratio = invalid / len(fields)

        signature = (msg.latitude, msg.longitude, msg.altitude)
        if self._last_signature is not None and signature == self._last_signature:
            self._stuck_count += 1
        else:
            self._stuck_count = 0
        self._last_signature = signature

        has_fix = self._status != NavSatStatus.STATUS_NO_FIX
        self._update_jump_speed(msg, has_fix)

        self._dropout_window.append(not has_fix)
        self._dropout_ratio = sum(self._dropout_window) / len(self._dropout_window)

    def _check_frame_id(self, frame_id: str) -> None:
        if self._last_frame_id is not None and frame_id != self._last_frame_id:
            self._frame_id_changed = True
        self._last_frame_id = frame_id

    def _update_jump_speed(self, msg: NavSatFix, has_fix: bool) -> None:
        """Speed implied by consecutive real fixes -- both current and previous must have a fix.

        A dropout's last-known-position artifact (frozen or reset to 0,0)
        would otherwise read as a huge, spurious "jump" the moment a real
        fix resumes; gating on `has_fix` for both ends avoids that.
        """
        stamp_seconds = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        if has_fix and self._last_fix is not None:
            last_stamp, last_lat, last_lon, last_alt = self._last_fix
            dt = stamp_seconds - last_stamp
            if dt > 0:
                horizontal = _haversine_distance_m(last_lat, last_lon, msg.latitude, msg.longitude)
                vertical = msg.altitude - last_alt
                self._jump_speed_mps = math.hypot(horizontal, vertical) / dt
        self._last_fix = (stamp_seconds, msg.latitude, msg.longitude, msg.altitude) if has_fix else None

    def get_status(self) -> CheckStatus:
        if self._message_count == 0:
            return CheckStatus(severity=Severity.OK, label="gnss", message="no messages received yet")

        fix_label = _FIX_STATUS_LABELS.get(self._status, str(self._status))
        fix_severity = Severity.ERROR if self._status == NavSatStatus.STATUS_NO_FIX else Severity.OK

        covariance_label = _COVARIANCE_TYPE_LABELS.get(self._covariance_type, str(self._covariance_type))
        covariance_known = self._covariance_type != NavSatFix.COVARIANCE_TYPE_UNKNOWN
        covariance_zone = self.thresholds.get("position_covariance")
        axis_severities = (
            [evaluate_zone(value, covariance_zone) for value in self._position_covariance]
            if covariance_known
            else [Severity.OK] * len(_POSITION_AXES)
        )
        covariance_severity = max(axis_severities)

        stuck_severity = evaluate_zone(float(self._stuck_count), self.thresholds.get("stuck_count"))
        invalid_severity = evaluate_zone(self._invalid_ratio, self.thresholds.get("invalid_ratio"))
        jump_severity = (
            evaluate_zone(self._jump_speed_mps, self.thresholds.get("jump_speed_mps"))
            if self._jump_speed_mps is not None
            else Severity.OK
        )
        dropout_severity = evaluate_zone(self._dropout_ratio, self.thresholds.get("fix_dropout_ratio"))
        frame_id_severity = Severity.WARN if self._frame_id_changed else Severity.OK

        worst = max(
            fix_severity,
            covariance_severity,
            stuck_severity,
            invalid_severity,
            jump_severity,
            dropout_severity,
            frame_id_severity,
        )

        problems = []
        if fix_severity != Severity.OK:
            problems.append("no fix")
        if covariance_severity != Severity.OK:
            bad_axes = [
                f"{axis}={value:.4g}"
                for axis, value, severity in zip(_POSITION_AXES, self._position_covariance, axis_severities)
                if severity != Severity.OK
            ]
            problems.append(f"position covariance out of bounds on {', '.join(bad_axes)}")
        if stuck_severity != Severity.OK:
            problems.append(f"stuck for {self._stuck_count} consecutive message(s)")
        if invalid_severity != Severity.OK:
            problems.append(f"invalid ratio {self._invalid_ratio:.2%}")
        if jump_severity != Severity.OK:
            problems.append(f"implausible jump at {self._jump_speed_mps:.3g} m/s")
        if dropout_severity != Severity.OK:
            problems.append(f"fix dropout ratio {self._dropout_ratio:.2%} over last {len(self._dropout_window)}")
        if frame_id_severity != Severity.OK:
            problems.append(f"frame_id changed (now '{self._last_frame_id}')")
        message = "; ".join(problems) if problems else f"{fix_label} nominal"

        # Per-axis covariance leads the Detail Panel -- it's the single most
        # actionable number here -- with x/y/z side by side (wide) or
        # stacked with 1-segment meters (narrow), same wide/compact split as
        # OdometryPlugin's own axis blocks. No paired lat/lon/alt the way
        # Odometry pairs a value with its variance: unlike a robot-frame
        # x/y/z position, a raw lat/lon isn't itself actionable here.
        values: dict[str, str] = {}
        if covariance_known:
            values["position_covariance"] = _covariance_block(
                self._position_covariance, covariance_zone, _METER_SEGMENTS, _AXIS_GAP
            )
            values["position_covariance_compact"] = _covariance_block(
                self._position_covariance, covariance_zone, _COMPACT_METER_SEGMENTS, "\n"
            )
        else:
            values["position_covariance"] = "n/a (covariance_type unknown)"

        values.update(
            {
                "fix_status": fix_label,
                "covariance_type": covariance_label,
                "invalid_ratio": f"{self._invalid_ratio:.4g}",
                "stuck_count": str(self._stuck_count),
                "jump_speed_mps": f"{self._jump_speed_mps:.4g}" if self._jump_speed_mps is not None else "n/a",
                "fix_dropout_ratio": f"{self._dropout_ratio:.4g}",
                "frame_id": self._last_frame_id or "",
            }
        )

        # Topic Panel "Fix" column: the single most at-a-glance number when
        # scanning several GNSS receivers at once -- colored by fix_severity
        # specifically (not `worst`), so a covariance/jump/dropout problem
        # on an otherwise-fixed receiver doesn't paint the fix label itself red.
        fix_color = SEVERITY_COLORS.get(fix_severity, "white")
        topic_panel_value = f"[{fix_color}]{fix_label}[/{fix_color}]"

        return CheckStatus(
            severity=worst,
            label="gnss",
            message=message,
            values=values,
            topic_panel_column="Fix",
            topic_panel_value=topic_panel_value,
        )
