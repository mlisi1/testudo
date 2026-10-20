"""Image / CompressedImage content-check plugin.

Covers sensor_msgs/msg/Image and sensor_msgs/msg/CompressedImage -- raw and
compressed camera frames are the same semantic question ("is this camera
feed healthy") wearing two different wire formats, so one plugin declares
both message types rather than splitting into two that would duplicate
frame_id/stuck-frame/payload-size logic for no real difference in what's
being checked.

Checks stay format-appropriate rather than requiring a real image decode (no
cv_bridge/OpenCV dependency, which Testudo doesn't otherwise need): frame_id
consistency, a stuck (byte-identical) frame, an empty payload, a raw Image's
data length disagreeing with height*step, a CompressedImage payload whose
magic bytes don't match its declared `format`, and a payload-size drop
relative to the topic's own rolling average -- a cheap proxy for a blank or
corrupted frame that works on compressed data without ever decompressing it.
Frame size and a rolling bandwidth estimate are also surfaced for the
Detail Panel, human-formatted (KB/MB) rather than as a raw byte count.

Both message types default to Testudo's presence-only tier (see
`presence_only_msg_types` and `subscription_manager.py`) when undeclared --
even a raw vitals subscription to camera data is heavy enough to compete
with the pipeline actually producing it. Declare a camera topic under
`topics:` to get this plugin's checks for it.
"""
from __future__ import annotations

import hashlib
from collections import deque
from typing import Any

from sensor_msgs.msg import CompressedImage, Image

from testudo.plugins.base import (
    SEVERITY_COLORS,
    CheckPlugin,
    CheckStatus,
    Severity,
    ThresholdZone,
    colorize,
    evaluate_zone,
)
from testudo.plugins.render import fmt_size, meter, sparkline

_IMAGE_TYPE = "sensor_msgs/msg/Image"
_COMPRESSED_IMAGE_TYPE = "sensor_msgs/msg/CompressedImage"

#: How many recent payload sizes to average for the size-drop check --
#: enough to smooth normal scene-to-scene compression variance without
#: reacting so slowly that a real fault takes a long time to surface.
_SIZE_HISTORY_LEN = 20

#: How many recent (timestamp, size) samples to span for the bandwidth
#: estimate. Note this measures the *checked* stream's throughput: on a
#: topic decimated below its real publish rate (see subscription_manager's
#: max_check_rate_hz), only every Nth frame's bytes ever reach on_message,
#: so this under-reports true wire bandwidth by roughly that same factor --
#: still a useful trend indicator, just not a bitrate meter.
_BANDWIDTH_WINDOW_LEN = 10

#: Magic bytes for the two compressed formats CompressedImage.format
#: commonly names ("jpeg" or "png", optionally with a codec suffix like
#: "png; compression..." -- REP 118 leaves the exact string to the driver).
#: A format this plugin doesn't recognize is left unchecked rather than
#: guessed at.
_JPEG_MAGIC = b"\xff\xd8"
_PNG_MAGIC = b"\x89PNG"


def _codec(fmt: str) -> str:
    """The codec a CompressedImage.format names -- "jpeg" for "rgb8; jpeg compressed bgr8" -- else the format as-is."""
    fmt_lower = fmt.lower()
    for codec in ("jpeg", "png", "zstd", "rvl"):
        if codec in fmt_lower:
            return codec
    return "jpeg" if "jpg" in fmt_lower else fmt


def _flipped(zone: ThresholdZone | None) -> ThresholdZone | None:
    """A higher-is-better ratio zone as the equivalent higher-is-worse "shortfall" zone (for `meter`)."""
    if zone is None:
        return None
    return ThresholdZone(*(1.0 - b if b is not None else None for b in (zone.green, zone.orange, zone.red)))


def _payload_signature(data: bytes) -> tuple[int, bytes]:
    """A cheap stuck-frame fingerprint: length plus a hash, not the full buffer.

    Comparing full frame buffers byte-for-byte would work too, but hashing
    keeps the retained state small regardless of resolution.
    """
    return (len(data), hashlib.sha1(data).digest())


def _check_image_shape(msg: Image) -> tuple[bool, str]:
    """True + detail if a raw Image's declared shape doesn't match its payload."""
    if msg.height <= 0 or msg.width <= 0:
        return True, f"non-positive declared size ({msg.width}x{msg.height})"
    expected_min = msg.height * msg.step
    if len(msg.data) < expected_min:
        return True, f"data length {len(msg.data)} shorter than height*step ({expected_min})"
    return False, ""


def _check_compressed_format(fmt: str, data: bytes) -> tuple[bool, str]:
    """True + detail if a CompressedImage's payload doesn't match its declared `format`."""
    if not fmt:
        return True, "empty format field"
    fmt_lower = fmt.lower()
    if ("jpeg" in fmt_lower or "jpg" in fmt_lower) and not data.startswith(_JPEG_MAGIC):
        return True, f"format '{fmt}' but payload isn't a JPEG (bad magic bytes)"
    if "png" in fmt_lower and not data.startswith(_PNG_MAGIC):
        return True, f"format '{fmt}' but payload isn't a PNG (bad magic bytes)"
    return False, ""


class ImageStreamPlugin(CheckPlugin):
    """Frame-integrity checks shared by raw and compressed camera topics."""

    def __init__(
        self,
        thresholds: dict[str, ThresholdZone] | None = None,
        related_topics: dict[str, str] | None = None,
    ) -> None:
        super().__init__(thresholds, related_topics)
        self._now = 0.0
        self._message_count = 0
        self._last_frame_id: str | None = None
        self._frame_id_changed = False
        self._last_signature: tuple[int, bytes] | None = None
        self._stuck_count = 0
        self._size_history: deque[int] = deque(maxlen=_SIZE_HISTORY_LEN)
        self._bandwidth_history: deque[tuple[float, int]] = deque(maxlen=_BANDWIDTH_WINDOW_LEN)
        self._last_size = 0
        self._empty_payload = False
        self._malformed = False
        self._malformed_detail = ""
        self._size_drop_ratio = 1.0
        # What the frames are: "jpeg" (compressed) or "640×480 rgb8 · step 1920" (raw).
        self._kind = ""
        self._compressed = False

    @classmethod
    def msg_types(cls) -> tuple[str, ...]:
        return (_IMAGE_TYPE, _COMPRESSED_IMAGE_TYPE)

    @classmethod
    def presence_only_msg_types(cls) -> frozenset[str]:
        # Both wire formats are heavy enough (raw frames, or several
        # compressed/codec variants per camera) that auto-subscribing every
        # undeclared one is what previously starved other nodes on a real
        # robot -- see subscription_manager.py's presence-tier docs.
        return frozenset({_IMAGE_TYPE, _COMPRESSED_IMAGE_TYPE})

    @classmethod
    def default_thresholds(cls) -> dict[str, ThresholdZone]:
        return {
            "stuck_count": ThresholdZone(green=2, orange=10),
            # Fraction of the topic's own rolling-average payload size --
            # lower is worse (a much smaller-than-usual frame is the signal).
            "size_drop_ratio": ThresholdZone(green=0.6, orange=0.3),
        }

    def on_tick(self, now_seconds: float) -> None:
        self._now = now_seconds

    def on_message(self, topic: str, msg: Any) -> None:
        self._message_count += 1
        self._check_frame_id(msg.header.frame_id)

        data = bytes(msg.data)
        self._last_size = len(data)
        self._empty_payload = self._last_size == 0

        if isinstance(msg, Image):
            self._malformed, self._malformed_detail = _check_image_shape(msg)
            self._kind = f"{msg.width}×{msg.height} {msg.encoding or '?'} · step {msg.step}"
            self._compressed = False
        elif isinstance(msg, CompressedImage):
            self._malformed, self._malformed_detail = _check_compressed_format(msg.format, data)
            self._kind = _codec(msg.format)
            self._compressed = True

        if self._empty_payload:
            return
        self._check_stuck(_payload_signature(data))
        self._check_size_drop()
        self._size_history.append(self._last_size)
        self._bandwidth_history.append((self._now, self._last_size))

    def _check_frame_id(self, frame_id: str) -> None:
        if self._last_frame_id is not None and frame_id != self._last_frame_id:
            self._frame_id_changed = True
        self._last_frame_id = frame_id

    def _check_stuck(self, signature: tuple[int, bytes]) -> None:
        if self._last_signature is not None and signature == self._last_signature:
            self._stuck_count += 1
        else:
            self._stuck_count = 0
        self._last_signature = signature

    def _check_size_drop(self) -> None:
        if not self._size_history:
            self._size_drop_ratio = 1.0
            return
        average = sum(self._size_history) / len(self._size_history)
        self._size_drop_ratio = (self._last_size / average) if average > 0 else 1.0

    def _bandwidth_bytes_per_second(self) -> float:
        if len(self._bandwidth_history) < 2:
            return 0.0
        span = self._bandwidth_history[-1][0] - self._bandwidth_history[0][0]
        if span <= 0:
            return 0.0
        return sum(size for _, size in self._bandwidth_history) / span

    def get_status(self) -> CheckStatus:
        if self._message_count == 0:
            return CheckStatus(severity=Severity.OK, label="image_stream", message="no messages received yet")

        empty_severity = Severity.ERROR if self._empty_payload else Severity.OK
        malformed_severity = Severity.ERROR if self._malformed else Severity.OK
        stuck_severity = evaluate_zone(float(self._stuck_count), self.thresholds.get("stuck_count"))
        size_drop_severity = evaluate_zone(
            self._size_drop_ratio, self.thresholds.get("size_drop_ratio"), higher_is_worse=False
        )
        frame_id_severity = Severity.WARN if self._frame_id_changed else Severity.OK

        worst = max(empty_severity, malformed_severity, stuck_severity, size_drop_severity, frame_id_severity)

        problems = []
        if empty_severity != Severity.OK:
            problems.append("empty payload")
        if malformed_severity != Severity.OK:
            problems.append(self._malformed_detail)
        if stuck_severity != Severity.OK:
            problems.append(f"stuck for {self._stuck_count} consecutive message(s)")
        if size_drop_severity != Severity.OK:
            problems.append(f"payload size dropped to {self._size_drop_ratio:.0%} of recent average")
        if frame_id_severity != Severity.OK:
            problems.append(f"frame_id changed (now '{self._last_frame_id}')")
        message = "; ".join(problems) if problems else fmt_size(self._last_size)

        codes: dict[str, str] = {}
        if empty_severity != Severity.OK:
            codes["IMG-001"] = colorize("empty payload", empty_severity)
        if malformed_severity != Severity.OK:
            codes["IMG-002"] = colorize(self._malformed_detail, malformed_severity)
        if stuck_severity != Severity.OK:
            codes["IMG-003"] = colorize(f"frame identical for {self._stuck_count} messages", stuck_severity)
        if size_drop_severity != Severity.OK:
            codes["IMG-004"] = colorize(f"frame size dropped to {self._size_drop_ratio:.0%} of average", size_drop_severity)
        if frame_id_severity != Severity.OK:
            codes["IMG-005"] = colorize(f"frame_id changed (now '{self._last_frame_id}')", frame_id_severity)

        size_zone = self.thresholds.get("size_drop_ratio")
        drop_meter = meter(1.0 - self._size_drop_ratio, _flipped(size_zone), 6)
        sizes = sparkline(self._size_history, size_drop_severity, floor=0.0)
        values = {
            "stream": (
                f"{self._kind}  {fmt_size(self._last_size)}/frame · "
                # Decimated: this is the checked frames' throughput, not the topic's.
                f"{fmt_size(self._bandwidth_bytes_per_second())}/s [dim]checked[/dim]"
            ),
            "sizes": (
                f"{sizes or '[dim]–[/dim]'} [dim](last {len(self._size_history)})[/dim]   "
                f"{colorize(f'{self._size_drop_ratio:.0%}', size_drop_severity)} of avg {drop_meter}"
            ).rstrip(),
            "stuck": str(self._stuck_count),
        }
        if self._compressed:
            values["payload"] = colorize("✗ " + self._malformed_detail, Severity.ERROR) if self._malformed else f"✓ {self._kind}"
        values["frame"] = (self._last_frame_id or "") + (" [dim](changed)[/dim]" if self._frame_id_changed else "")

        # Topic Panel "Size" column: the single most informative number for
        # scanning several camera topics at once (a frozen or collapsed
        # bitrate shows up here immediately), colored by the topic's overall
        # status rather than any one component check.
        color = SEVERITY_COLORS.get(worst, "white")
        topic_panel_value = f"[{color}]{fmt_size(self._last_size)}[/{color}]"

        return CheckStatus(
            severity=worst,
            label="image_stream",
            message=message,
            values=values,
            topic_panel_column="Size",
            topic_panel_value=topic_panel_value,
            codes=codes,
            metrics=self._metrics(),
        )

    def _metrics(self) -> dict[str, float | str]:
        """The published numbers: frame size, checked throughput, size vs average, stuck/empty/malformed."""
        return {
            "frame_bytes": self._last_size,
            # Decimated: the checked frames' throughput, not the topic's (see the "stream" value).
            "checked_bandwidth_bytes_per_s": self._bandwidth_bytes_per_second(),
            "size_drop_ratio": self._size_drop_ratio,
            "stuck_count": self._stuck_count,
            "empty": self._empty_payload,
            "malformed": self._malformed,
            "format": self._kind,
            "frame_id": self._last_frame_id or "",
        }
