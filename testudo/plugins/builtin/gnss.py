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
    CheckPlugin,
    CheckStatus,
    Severity,
    ThresholdZone,
    colorize,
    evaluate_zone,
)
from testudo.plugins.builtin.gnss_layout import (
    fix_history,
    horizontal_vertical,
    position_line,
    services,
    short_fix_label,
    sigma_cell,
    sigma_h,
)
from testudo.plugins.render import downsample, meter, sparkline

_NAVSATFIX_TYPE = "sensor_msgs/msg/NavSatFix"

#: position_covariance is a row-major 3x3 matrix (ENU x/y/z); these are its
#: diagonal (variance) entries, in x/y/z order.
_POSITION_COVARIANCE_DIAG_INDICES = (0, 4, 8)
_POSITION_AXES = ("x", "y", "z")

_EARTH_RADIUS_M = 6371000.0

_METER_SEGMENTS = 6

#: The σh / jump-speed trends: one sample per this many seconds, this many kept.
_TREND_SAMPLE_SECONDS = 1.0
_TREND_SIZE = 60
#: Cells a trend sparkline is averaged down to.
_TREND_WIDTH = 20

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
        self._position: tuple[float, float, float] = (0.0, 0.0, 0.0)
        self._service = 0
        self._sigma_h_trend: deque[float] = deque(maxlen=_TREND_SIZE)
        self._jump_trend: deque[float] = deque(maxlen=_TREND_SIZE)
        self._last_trend_sample: float | None = None

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

    def on_tick(self, now_seconds: float) -> None:
        if self._message_count == 0:
            return
        if self._last_trend_sample is None or now_seconds - self._last_trend_sample >= _TREND_SAMPLE_SECONDS:
            self._sigma_h_trend.append(sigma_h(self._position_covariance) if self._covariance_known() else math.nan)
            self._jump_trend.append(self._jump_speed_mps if self._jump_speed_mps is not None else math.nan)
            self._last_trend_sample = now_seconds

    def _covariance_known(self) -> bool:
        return self._covariance_type != NavSatFix.COVARIANCE_TYPE_UNKNOWN

    def on_message(self, topic: str, msg: Any) -> None:
        self._message_count += 1
        self._check_frame_id(msg.header.frame_id)

        self._status = msg.status.status
        self._service = msg.status.service
        self._position = (msg.latitude, msg.longitude, msg.altitude)
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
        covariance_known = self._covariance_known()
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

        codes: dict[str, str] = {}
        if fix_severity != Severity.OK:
            codes["GNSS-001"] = colorize("no fix", fix_severity)
        if covariance_severity != Severity.OK:
            codes["GNSS-002"] = colorize(
                f"position σ high ({', '.join(bad_axes)} m²; per-axis limit {covariance_zone.orange:g})"
                if covariance_zone is not None and covariance_zone.orange is not None
                else f"position σ high ({', '.join(bad_axes)} m²)",
                covariance_severity,
            )
        if dropout_severity != Severity.OK:
            codes["GNSS-003"] = colorize(f"fix dropped in {sum(self._dropout_window)}/{len(self._dropout_window)}", dropout_severity)
        if jump_severity != Severity.OK:
            codes["GNSS-004"] = colorize(f"implausible jump at {self._jump_speed_mps:.3g} m/s", jump_severity)
        if stuck_severity != Severity.OK:
            codes["GNSS-005"] = colorize(f"fix identical for {self._stuck_count} messages", stuck_severity)
        if invalid_severity != Severity.OK:
            codes["GNSS-006"] = colorize(f"NaN/Inf in {self._invalid_ratio:.0%} of lat/lon/alt", invalid_severity)
        if frame_id_severity != Severity.OK:
            codes["GNSS-007"] = colorize(f"frame_id changed (now '{self._last_frame_id}')", frame_id_severity)

        # σh/σv instead of three per-axis chunks: people think in "±26 cm
        # horizontal". Each meter judges the worst single axis it
        # summarizes against the per-axis zone, so the meter and the
        # verdict above always agree.
        var_h, var_v = horizontal_vertical(self._position_covariance)
        var_x, var_y, _var_z = self._position_covariance
        fix_chip = colorize(f"● {fix_label}", fix_severity)
        horizontal = sigma_cell("σ horiz", var_h if covariance_known else None, covariance_zone, max(var_x, var_y))
        vertical = sigma_cell("σ vert", var_v if covariance_known else None, covariance_zone, var_v)
        jump = (
            f"{self._jump_speed_mps:.2f} m/s {meter(self._jump_speed_mps, self.thresholds.get('jump_speed_mps'), _METER_SEGMENTS)}"
            if self._jump_speed_mps is not None
            else "[dim]–[/dim]"
        )
        values: dict[str, str] = {
            "fix": f"{fix_chip}   {horizontal}   {vertical}   [dim]cov {covariance_label}[/dim]",
            "fixes": fix_history(self._dropout_window, dropout_severity),
            "jump": jump,
            "stuck": str(self._stuck_count),
            "invalid": f"{self._invalid_ratio:.0%}",
            "σh_more": f"{sparkline(downsample(self._sigma_h_trend, _TREND_WIDTH), covariance_severity, floor=0.0) or '[dim]–[/dim]'} [dim]({len(self._sigma_h_trend)} s)[/dim]",
            "position": position_line(*self._position),
            "frame": self._last_frame_id or "",
            "services_more": services(self._service),
            "jump_trend_more": sparkline(downsample(self._jump_trend, _TREND_WIDTH), jump_severity, floor=0.0) or "[dim]–[/dim]",
        }

        # Topic Panel "Fix" column: the fix label (colored by fix_severity
        # only, so a covariance problem on a fixed receiver doesn't paint
        # the fix itself red) plus σh, the number that says how good it is.
        sigma = sigma_h(self._position_covariance)
        topic_panel_value = colorize(short_fix_label(fix_label), fix_severity)
        if covariance_known:
            topic_panel_value += colorize(f" ±{sigma:.2f}" if sigma < 10 else f" ±{sigma:.0f}", covariance_severity)

        return CheckStatus(
            severity=worst,
            label="gnss",
            message=message,
            values=values,
            topic_panel_column="Fix",
            topic_panel_value=topic_panel_value,
            codes=codes,
            metrics=self._metrics(fix_label, covariance_label, covariance_known),
        )

    def _metrics(self, fix_label: str, covariance_label: str, covariance_known: bool) -> dict[str, float | str]:
        """The published numbers: fix status, position, σ (when the receiver reports it), dropout/jump/stuck/invalid."""
        latitude, longitude, altitude = self._position
        metrics: dict[str, float | str] = {
            # The NavSatStatus code (-1 no fix, 0 fix, 1 SBAS, 2 GBAS): plottable as a step line.
            "fix_status": self._status,
            "fix": fix_label,
            "service": self._service,
            "latitude_deg": latitude,
            "longitude_deg": longitude,
            "altitude_m": altitude,
            "covariance_type": covariance_label,
            "fix_dropout_ratio": self._dropout_ratio,
            "stuck_count": self._stuck_count,
            "invalid_ratio": self._invalid_ratio,
            "frame_id": self._last_frame_id or "",
        }
        if covariance_known:
            var_h, var_v = horizontal_vertical(self._position_covariance)
            metrics["sigma_horizontal_m"] = math.sqrt(max(0.0, var_h))
            metrics["sigma_vertical_m"] = math.sqrt(max(0.0, var_v))
            for axis, variance in zip(_POSITION_AXES, self._position_covariance):
                metrics[f"position_variance_{axis}_m2"] = variance
        if self._jump_speed_mps is not None:
            metrics["jump_speed_m_per_s"] = self._jump_speed_mps
        return metrics
