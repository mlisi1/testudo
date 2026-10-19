"""LaserScan / PointCloud2 (2D + 3D lidar) content-check plugin.

Covers sensor_msgs/msg/LaserScan (2D) and sensor_msgs/msg/PointCloud2 (3D)
-- both are the same semantic question ("is this range sensor's data
sane") wearing different wire formats, so one plugin declares both rather
than splitting into two that would duplicate stuck-reading/frame_id logic.

The two formats get different default tiering, though: an undeclared
LaserScan topic gets Testudo's normal full tier (2D scans are small and
cheap), but an undeclared PointCloud2 topic defaults to presence-only (see
`presence_only_msg_types`) -- a dense 3D point cloud is exactly the kind
of large payload that measurably competes with other nodes for CPU/
transport bandwidth if auto-subscribed on every undeclared topic, the same
reasoning `image_stream.py` already applies to camera frames. Declaring a
PointCloud2 topic under `topics:` opts it into this plugin's checks like
any other declared topic.

LaserScan checks: NaN/-Inf/out-of-range values (REP 117: +Inf is a valid
"no obstacle in range" reading), an all-zero scan, a stuck (byte-for-byte
identical) scan, and a frame_id that changes between messages.

PointCloud2 checks stay cheap and dependency-light (`sensor_msgs_py`, a
`sensor_msgs` companion package, plus numpy -- both already Testudo
dependencies; no PCL, no real geometry processing): a malformed cloud
(declared height*row_step longer than the actual payload), an empty
payload, a NaN/Inf ratio sampled from the x/y/z fields (skipped if the
cloud doesn't publish them), a stuck (byte-for-byte identical) cloud, and
frame_id consistency. A cloud declaring `is_dense: false` (typical of an
organized lidar cloud, e.g. a VLP-16's 16 rings) is *allowed* to carry NaN
points -- that's how a beam with no return keeps its slot in the grid --
so those count as a reported `no_return_ratio`, not as invalid; only Inf
is invalid there. A cloud claiming `is_dense: true` has no such excuse.
"""
from __future__ import annotations

import hashlib
import math
from typing import Any

import numpy as np
from sensor_msgs.msg import LaserScan, PointCloud2
from sensor_msgs_py.point_cloud2 import read_points

from testudo.plugins.base import SEVERITY_COLORS, CheckPlugin, CheckStatus, Severity, ThresholdZone, colorize, evaluate_zone
from testudo.plugins.builtin.point_stream_layout import (
    format_points,
    profile_line,
    returns_bar,
    returns_text,
    scan_profile,
)
from testudo.plugins.render import fmt_size

_LASER_SCAN_TYPE = "sensor_msgs/msg/LaserScan"
_POINT_CLOUD_TYPE = "sensor_msgs/msg/PointCloud2"

#: x/y/z are the only fields checked for NaN/Inf -- present on essentially
#: every 3D lidar/depth-camera point cloud, and enough to catch the same
#: class of sensor fault (a frozen/corrupted driver spewing garbage floats)
#: without decoding intensity/color/ring fields this plugin has no opinion
#: about.
_COORDINATE_FIELD_NAMES = ("x", "y", "z")


def _payload_signature(data: bytes) -> tuple[int, bytes]:
    """A cheap stuck-cloud fingerprint: length plus a hash, not the full buffer."""
    return (len(data), hashlib.sha1(data).digest())


def _check_point_cloud_shape(msg: PointCloud2) -> tuple[bool, str]:
    """True + detail if a PointCloud2's declared shape doesn't match its payload."""
    if msg.height <= 0 or msg.width <= 0:
        return True, f"non-positive declared size ({msg.width}x{msg.height})"
    expected_min = msg.height * msg.row_step
    if len(msg.data) < expected_min:
        return True, f"data length {len(msg.data)} shorter than height*row_step ({expected_min})"
    return False, ""


def _invalid_and_no_return_ratios(msg: PointCloud2) -> tuple[float, float]:
    """(invalid, no-return) fractions of points, judged on the x/y/z coordinates.

    A NaN coordinate is a no-return placeholder in a cloud declaring
    `is_dense: false`, and invalid in one claiming `is_dense: true`; an
    infinite coordinate is always invalid. (0.0, 0.0) if the cloud has
    none of the coordinate fields.
    """
    present = {field.name for field in msg.fields}
    field_names = [name for name in _COORDINATE_FIELD_NAMES if name in present]
    if not field_names:
        return 0.0, 0.0
    try:
        points = read_points(msg, field_names=field_names, skip_nans=False)
    except Exception:
        # A malformed cloud (bad field/offset/datatype combination) is
        # already flagged by `_check_point_cloud_shape`'s length check, or
        # close enough to it -- don't also raise out of a content check.
        return 0.0, 0.0
    if points.size == 0:
        return 0.0, 0.0
    coords = np.stack([points[name].astype(np.float64) for name in field_names], axis=-1)
    total = coords.shape[0]
    non_finite = np.count_nonzero(~np.all(np.isfinite(coords), axis=-1))
    if msg.is_dense:
        return float(non_finite) / total, 0.0
    infinite = np.count_nonzero(np.any(np.isinf(coords), axis=-1))
    return float(infinite) / total, float(non_finite - infinite) / total


class PointStreamPlugin(CheckPlugin):
    """Range/point sanity checks shared by 2D LaserScan and 3D PointCloud2 topics."""

    def __init__(
        self,
        thresholds: dict[str, ThresholdZone] | None = None,
        related_topics: dict[str, str] | None = None,
    ) -> None:
        super().__init__(thresholds, related_topics)
        self._message_count = 0
        self._last_frame_id: str | None = None
        self._frame_id_changed = False
        self._last_signature: Any = None
        self._stuck_count = 0
        self._invalid_ratio = 0.0
        self._no_return_ratio: float | None = None
        self._plausibility_ok = True
        self._malformed = False
        self._malformed_detail = ""
        self._empty_payload = False
        self._all_zero = False
        self._detail = "no messages received yet"
        # Shares of beams/points per class for the returns bar:
        # (valid, no return, out of range, invalid).
        self._shares = (0.0, 0.0, 0.0, 0.0)
        self._points = 0
        # LaserScan only: the angular profile and its span line.
        self._profile: str | None = None
        self._beams: str | None = None
        # PointCloud2 only: shape, density, fields and payload size.
        self._cloud: str | None = None

    @classmethod
    def msg_types(cls) -> tuple[str, ...]:
        return (_LASER_SCAN_TYPE, _POINT_CLOUD_TYPE)

    @classmethod
    def presence_only_msg_types(cls) -> frozenset[str]:
        # A dense 3D point cloud is heavy the same way camera frames are --
        # see subscription_manager.py's presence-tier docs. A 2D LaserScan
        # is small and stays on the normal full tier by default.
        return frozenset({_POINT_CLOUD_TYPE})

    @classmethod
    def default_thresholds(cls) -> dict[str, ThresholdZone]:
        return {
            "invalid_ratio": ThresholdZone(green=0.01, orange=0.05),
            "stuck_count": ThresholdZone(green=2, orange=10),
        }

    def on_message(self, topic: str, msg: Any) -> None:
        self._message_count += 1
        self._check_frame_id(msg.header.frame_id)

        if isinstance(msg, LaserScan):
            self._on_laser_scan(msg)
        elif isinstance(msg, PointCloud2):
            self._on_point_cloud(msg)

    def _check_frame_id(self, frame_id: str) -> None:
        if self._last_frame_id is not None and frame_id != self._last_frame_id:
            self._frame_id_changed = True
        self._last_frame_id = frame_id

    def _check_stuck(self, signature: Any) -> None:
        if self._last_signature is not None and signature == self._last_signature:
            self._stuck_count += 1
        else:
            self._stuck_count = 0
        self._last_signature = signature

    def _on_laser_scan(self, msg: LaserScan) -> None:
        ranges = np.asarray(msg.ranges, dtype=np.float64)
        total = ranges.size
        # REP 117: +Inf means "no obstacle detected within range" (valid);
        # NaN and -Inf aren't standard readings and count as invalid.
        invalid = int(np.count_nonzero(np.isnan(ranges) | np.isneginf(ranges)))
        no_return = int(np.count_nonzero(np.isposinf(ranges)))
        finite = ranges[np.isfinite(ranges)]
        out_of_bounds = int(np.count_nonzero((finite < msg.range_min) | (finite > msg.range_max)))
        self._invalid_ratio = (invalid + out_of_bounds) / total if total else 0.0
        self._plausibility_ok = out_of_bounds == 0
        self._points = total
        if total:
            valid = total - invalid - no_return - out_of_bounds
            self._shares = (valid / total, no_return / total, out_of_bounds / total, invalid / total)

        self._all_zero = total > 0 and not np.any(ranges)
        self._detail = "all-zero scan" if self._all_zero else f"{invalid} invalid, {out_of_bounds} out-of-bounds of {total}"
        self._profile = profile_line(
            scan_profile(ranges, msg.range_max), msg.angle_min, msg.angle_max, msg.range_min, msg.range_max
        )
        self._beams = f"{total} @ {math.degrees(abs(msg.angle_increment)):.3g}°"

        self._check_stuck(tuple(msg.ranges))

    def _on_point_cloud(self, msg: PointCloud2) -> None:
        data = bytes(msg.data)
        point_count = msg.width * msg.height
        self._empty_payload = len(data) == 0 or point_count == 0
        self._malformed, self._malformed_detail = _check_point_cloud_shape(msg)

        if self._empty_payload:
            self._detail = "empty point cloud"
            return

        self._invalid_ratio, self._no_return_ratio = _invalid_and_no_return_ratios(msg)
        self._points = point_count
        self._shares = (1.0 - self._invalid_ratio - self._no_return_ratio, self._no_return_ratio, 0.0, self._invalid_ratio)
        shape = f"organized {msg.width}×{msg.height}" if msg.height > 1 else "unorganized"
        dense = "dense ✓" if msg.is_dense else "[dim]dense ✗[/dim]"
        fields = " ".join(field.name for field in msg.fields)
        self._cloud = f"{shape}  {dense}  [dim]fields[/dim] {fields}  [dim]{fmt_size(len(data))}[/dim]"
        self._detail = (
            f"{point_count} point(s), {self._no_return_ratio:.1%} no return, {self._invalid_ratio:.2%} invalid"
        )
        self._check_stuck(_payload_signature(data))

    def get_status(self) -> CheckStatus:
        if self._message_count == 0:
            return CheckStatus(severity=Severity.OK, label="point_stream", message="no messages received yet")

        empty_severity = Severity.ERROR if self._empty_payload else Severity.OK
        malformed_severity = Severity.ERROR if self._malformed else Severity.OK
        invalid_severity = evaluate_zone(self._invalid_ratio, self.thresholds.get("invalid_ratio"))
        stuck_severity = evaluate_zone(float(self._stuck_count), self.thresholds.get("stuck_count"))
        plausibility_severity = Severity.OK if self._plausibility_ok else Severity.WARN
        frame_id_severity = Severity.WARN if self._frame_id_changed else Severity.OK

        worst = max(
            empty_severity, malformed_severity, invalid_severity, stuck_severity, plausibility_severity, frame_id_severity
        )

        problems = []
        if empty_severity != Severity.OK:
            problems.append("empty payload")
        if malformed_severity != Severity.OK:
            problems.append(self._malformed_detail)
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
            codes["SENS-001"] = colorize(f"invalid readings {self._invalid_ratio:.1%}", invalid_severity)
        if stuck_severity != Severity.OK:
            codes["SENS-002"] = colorize(f"identical for {self._stuck_count} messages", stuck_severity)
        if self._all_zero:
            codes["SENS-003"] = colorize("all-zero scan", Severity.WARN)
        if plausibility_severity != Severity.OK:
            codes["SENS-004"] = colorize("ranges outside range_min..range_max", plausibility_severity)
        if frame_id_severity != Severity.OK:
            codes["SENS-005"] = colorize(f"frame_id changed (now '{self._last_frame_id}')", frame_id_severity)
        if empty_severity != Severity.OK:
            codes["SENS-006"] = colorize("empty point cloud", empty_severity)
        if malformed_severity != Severity.OK:
            codes["SENS-007"] = colorize(self._malformed_detail, malformed_severity)

        values: dict[str, str] = {}
        if self._cloud is not None:
            values["cloud"] = f"{self._points:,} pts  {self._cloud}"
        if not self._empty_payload:
            values["returns"] = f"{returns_bar(*self._shares)}  {returns_text(*self._shares, invalid_severity)}"
        if self._profile is not None:
            values["profile"] = self._profile
        if self._beams is not None:
            values["beams"] = self._beams
        values["stuck"] = str(self._stuck_count)
        values["frame"] = self._last_frame_id or ""

        color = SEVERITY_COLORS.get(worst, "white")
        return CheckStatus(
            severity=worst,
            label="point_stream",
            message=message,
            values=values,
            topic_panel_column="Points",
            topic_panel_value=f"[{color}]{format_points(self._points)}[/{color}]",
            codes=codes,
        )
