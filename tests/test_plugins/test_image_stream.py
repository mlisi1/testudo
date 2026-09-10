"""Unit tests for ImageStreamPlugin -- synthetic Image/CompressedImage, no live ROS graph."""
from __future__ import annotations

from sensor_msgs.msg import CompressedImage, Image

from testudo.plugins.base import Severity
from testudo.plugins.builtin.image_stream import ImageStreamPlugin

_JPEG_MAGIC = b"\xff\xd8"
_PNG_MAGIC = b"\x89PNG"


def _image(
    width: int = 4, height: int = 2, step: int | None = None, frame_id: str = "camera", fill: int = 0x42
) -> Image:
    msg = Image()
    msg.header.frame_id = frame_id
    msg.width = width
    msg.height = height
    msg.step = step if step is not None else width
    msg.data = bytes([fill]) * (msg.height * msg.step)
    return msg


def _compressed(fmt: str = "jpeg", payload: bytes | None = None, frame_id: str = "camera") -> CompressedImage:
    msg = CompressedImage()
    msg.header.frame_id = frame_id
    msg.format = fmt
    msg.data = payload if payload is not None else (_JPEG_MAGIC + b"\x00" * 100)
    return msg


def test_no_messages_yet_is_ok() -> None:
    plugin = ImageStreamPlugin()
    status = plugin.get_status()
    assert status.severity == Severity.OK
    assert "no messages received" in status.message


# --- Image -------------------------------------------------------------


def test_image_nominal_is_ok() -> None:
    plugin = ImageStreamPlugin()
    plugin.on_message("/camera/image_raw", _image())
    assert plugin.get_status().severity == Severity.OK


def test_image_empty_payload_is_error() -> None:
    plugin = ImageStreamPlugin()
    msg = _image()
    msg.data = b""
    plugin.on_message("/camera/image_raw", msg)
    status = plugin.get_status()
    assert status.severity == Severity.ERROR
    assert "empty payload" in status.message


def test_image_data_shorter_than_declared_shape_is_malformed() -> None:
    plugin = ImageStreamPlugin()
    msg = _image(width=4, height=2, step=4)
    msg.data = bytes([0x00]) * 3  # short of height*step == 8
    plugin.on_message("/camera/image_raw", msg)
    status = plugin.get_status()
    assert status.severity == Severity.ERROR
    assert "shorter than height*step" in status.message


def test_image_stuck_frame_escalates_after_repeats() -> None:
    plugin = ImageStreamPlugin()
    frame = _image()
    for _ in range(4):
        plugin.on_message("/camera/image_raw", frame)
    status = plugin.get_status()
    assert status.severity == Severity.WARN
    assert "stuck for" in status.message
    assert status.values["stuck_count"] == "3"


def test_image_frame_id_change_is_flagged() -> None:
    plugin = ImageStreamPlugin()
    plugin.on_message("/camera/image_raw", _image(frame_id="camera_a"))
    plugin.on_message("/camera/image_raw", _image(frame_id="camera_b"))
    status = plugin.get_status()
    assert status.severity == Severity.WARN
    assert "frame_id changed" in status.message


def test_image_topic_panel_shows_size_column() -> None:
    plugin = ImageStreamPlugin()
    plugin.on_message("/camera/image_raw", _image())
    status = plugin.get_status()
    assert status.topic_panel_column == "Size"
    assert "KB" in status.topic_panel_value or "MB" in status.topic_panel_value


def test_detail_values_report_human_readable_size() -> None:
    plugin = ImageStreamPlugin()
    plugin.on_message("/camera/image_raw", _image())
    size = plugin.get_status().values["size"]
    assert "KB" in size or "MB" in size
    assert "bytes" not in size


def test_bandwidth_defaults_to_zero_without_on_tick() -> None:
    # on_message alone (no on_tick between calls) never advances `_now`, so
    # every sample lands at the same timestamp and the window has no span.
    plugin = ImageStreamPlugin()
    for _ in range(3):
        plugin.on_message("/camera/image_raw", _image())
    assert plugin.get_status().values["bandwidth"] == "0.0KB/s"


def test_bandwidth_estimated_from_on_tick_timestamps() -> None:
    plugin = ImageStreamPlugin()
    frame = _compressed(payload=_JPEG_MAGIC + b"\x00" * 10_000)
    for second in range(5):
        plugin.on_tick(float(second))
        plugin.on_message("/camera/image_raw/compressed", frame)
    bandwidth = plugin.get_status().values["bandwidth"]
    assert bandwidth.endswith("KB/s")
    assert bandwidth != "0.0KB/s"


# --- CompressedImage -----------------------------------------------------


def test_compressed_nominal_is_ok() -> None:
    plugin = ImageStreamPlugin()
    plugin.on_message("/camera/image_raw/compressed", _compressed())
    assert plugin.get_status().severity == Severity.OK


def test_compressed_empty_format_is_malformed() -> None:
    plugin = ImageStreamPlugin()
    plugin.on_message("/camera/image_raw/compressed", _compressed(fmt=""))
    status = plugin.get_status()
    assert status.severity == Severity.ERROR
    assert "empty format" in status.message


def test_compressed_jpeg_format_with_bad_magic_bytes_is_malformed() -> None:
    plugin = ImageStreamPlugin()
    plugin.on_message("/camera/image_raw/compressed", _compressed(fmt="jpeg", payload=b"\x00" * 100))
    status = plugin.get_status()
    assert status.severity == Severity.ERROR
    assert "bad magic bytes" in status.message


def test_compressed_png_format_with_correct_magic_bytes_is_ok() -> None:
    plugin = ImageStreamPlugin()
    plugin.on_message("/camera/image_raw/compressed", _compressed(fmt="png", payload=_PNG_MAGIC + b"\x00" * 100))
    assert plugin.get_status().severity == Severity.OK


def test_compressed_unknown_format_is_left_unchecked() -> None:
    plugin = ImageStreamPlugin()
    plugin.on_message("/camera/image_raw/compressed", _compressed(fmt="h264", payload=b"\x00" * 100))
    assert plugin.get_status().severity == Severity.OK


def test_compressed_size_drop_is_flagged() -> None:
    plugin = ImageStreamPlugin()
    for _ in range(10):
        plugin.on_message("/camera/image_raw/compressed", _compressed(payload=_JPEG_MAGIC + b"\x00" * 10_000))
    # A frame far smaller than the established rolling average -- possible
    # blank/corrupt frame -- without ever decompressing either payload.
    plugin.on_message("/camera/image_raw/compressed", _compressed(payload=_JPEG_MAGIC + b"\x00" * 10))
    status = plugin.get_status()
    assert status.severity == Severity.ERROR
    assert "payload size dropped" in status.message


def test_default_thresholds_cover_both_metrics() -> None:
    thresholds = ImageStreamPlugin.default_thresholds()
    assert set(thresholds) == {"stuck_count", "size_drop_ratio"}


def test_both_wire_formats_default_to_presence_only() -> None:
    assert ImageStreamPlugin.presence_only_msg_types() == frozenset(
        {"sensor_msgs/msg/Image", "sensor_msgs/msg/CompressedImage"}
    )
