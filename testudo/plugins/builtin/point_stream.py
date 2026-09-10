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
frame_id consistency.
"""
from __future__ import annotations

import hashlib
import math
from typing import Any

import numpy as np
from sensor_msgs.msg import LaserScan, PointCloud2
from sensor_msgs_py.point_cloud2 import read_points

from testudo.plugins.base import CheckPlugin, CheckStatus, Severity, ThresholdZone, evaluate_zone

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


def _invalid_point_ratio(msg: PointCloud2) -> float:
    """Fraction of points with a non-finite x/y/z coordinate, or 0.0 if the cloud has none of those fields."""
    present = {field.name for field in msg.fields}
    field_names = [name for name in _COORDINATE_FIELD_NAMES if name in present]
    if not field_names:
        return 0.0
    try:
        points = read_points(msg, field_names=field_names, skip_nans=False)
    except Exception:
        # A malformed cloud (bad field/offset/datatype combination) is
        # already flagged by `_check_point_cloud_shape`'s length check, or
        # close enough to it -- don't also raise out of a content check.
        return 0.0
    if points.size == 0:
        return 0.0
    coords = np.stack([points[name].astype(np.float64) for name in field_names], axis=-1)
    finite_per_point = np.all(np.isfinite(coords), axis=-1)
    return float(np.count_nonzero(~finite_per_point)) / coords.shape[0]


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
        self._plausibility_ok = True
        self._malformed = False
        self._malformed_detail = ""
        self._empty_payload = False
        self._detail = "no messages received yet"

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
        ranges = msg.ranges
        total = len(ranges)
        # REP 117: +Inf means "no obstacle detected within range" (valid);
        # NaN and -Inf aren't standard readings and count as invalid.
        invalid = sum(1 for r in ranges if math.isnan(r) or r == float("-inf"))
        out_of_bounds = sum(
            1 for r in ranges if not math.isnan(r) and not math.isinf(r) and not (msg.range_min <= r <= msg.range_max)
        )
        self._invalid_ratio = (invalid + out_of_bounds) / total if total else 0.0
        self._plausibility_ok = out_of_bounds == 0

        all_zero = total > 0 and all(r == 0.0 for r in ranges)
        self._detail = "all-zero scan" if all_zero else f"{invalid} invalid, {out_of_bounds} out-of-bounds of {total}"

        self._check_stuck(tuple(ranges))

    def _on_point_cloud(self, msg: PointCloud2) -> None:
        data = bytes(msg.data)
        point_count = msg.width * msg.height
        self._empty_payload = len(data) == 0 or point_count == 0
        self._malformed, self._malformed_detail = _check_point_cloud_shape(msg)

        if self._empty_payload:
            self._detail = "empty point cloud"
            return

        self._invalid_ratio = _invalid_point_ratio(msg)
        self._detail = f"{point_count} point(s), {self._invalid_ratio:.2%} invalid"
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

        values = {
            "invalid_ratio": f"{self._invalid_ratio:.4g}",
            "stuck_count": str(self._stuck_count),
            "frame_id": self._last_frame_id or "",
            "detail": self._detail,
        }
        return CheckStatus(severity=worst, label="point_stream", message=message, values=values)
