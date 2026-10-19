"""Unit tests for PointStreamPlugin -- synthetic LaserScan/PointCloud2, no live ROS graph."""
from __future__ import annotations

from sensor_msgs.msg import LaserScan, PointCloud2, PointField
from sensor_msgs_py.point_cloud2 import create_cloud_xyz32
from std_msgs.msg import Header

from rich.text import Text

from testudo.plugins.base import Severity
from testudo.plugins.builtin.point_stream import PointStreamPlugin


def _legend(status) -> str:
    """The returns bar's legend: the plain text after the bar."""
    return Text.from_markup(status.values["returns"]).plain.split("  ", 1)[1]


def _laser_scan(ranges: list[float], range_min: float = 0.0, range_max: float = 10.0, frame_id: str = "laser") -> LaserScan:
    msg = LaserScan()
    msg.header.frame_id = frame_id
    msg.range_min = range_min
    msg.range_max = range_max
    msg.ranges = ranges
    return msg


def _point_cloud(points: list[tuple[float, float, float]], frame_id: str = "lidar", is_dense: bool = True):
    header = Header()
    header.frame_id = frame_id
    cloud = create_cloud_xyz32(header, points)
    cloud.is_dense = is_dense
    return cloud


def test_no_messages_yet_is_ok() -> None:
    plugin = PointStreamPlugin()
    status = plugin.get_status()
    assert status.severity == Severity.OK
    assert "no messages received" in status.message


def test_msg_types_covers_2d_and_3d() -> None:
    assert PointStreamPlugin.msg_types() == ("sensor_msgs/msg/LaserScan", "sensor_msgs/msg/PointCloud2")


def test_only_point_cloud_defaults_to_presence_only() -> None:
    """2D LaserScan stays full-tier by default; only the heavy 3D PointCloud2 downgrades."""
    presence_only = PointStreamPlugin.presence_only_msg_types()
    assert presence_only == frozenset({"sensor_msgs/msg/PointCloud2"})
    assert "sensor_msgs/msg/LaserScan" not in presence_only


# --- LaserScan (2D) ----------------------------------------------------------


def test_laser_scan_nominal_is_ok() -> None:
    plugin = PointStreamPlugin()
    plugin.on_message("/scan", _laser_scan([1.0, 2.0, 3.0]))
    assert plugin.get_status().severity == Severity.OK


def test_laser_scan_nan_counts_as_invalid() -> None:
    plugin = PointStreamPlugin()
    ranges = [1.0] * 9 + [float("nan")]  # 1/10 = 0.1 > default orange 0.05
    plugin.on_message("/scan", _laser_scan(ranges))
    status = plugin.get_status()
    assert status.severity == Severity.ERROR
    assert "invalid ratio" in status.message


def test_laser_scan_positive_inf_is_valid_per_rep117() -> None:
    plugin = PointStreamPlugin()
    ranges = [1.0] * 9 + [float("inf")]
    plugin.on_message("/scan", _laser_scan(ranges))
    status = plugin.get_status()
    assert status.severity == Severity.OK
    assert _legend(status) == "90% valid · 10% no return · 0% invalid"


def test_laser_scan_negative_inf_counts_as_invalid() -> None:
    plugin = PointStreamPlugin()
    ranges = [1.0] * 9 + [float("-inf")]
    plugin.on_message("/scan", _laser_scan(ranges))
    status = plugin.get_status()
    assert status.severity == Severity.ERROR


def test_laser_scan_out_of_bounds_value_flags_plausibility() -> None:
    plugin = PointStreamPlugin()
    plugin.on_message("/scan", _laser_scan([1.0, 2.0, 50.0], range_min=0.0, range_max=10.0))
    status = plugin.get_status()
    assert status.severity != Severity.OK
    assert "outside plausible range" in status.message


def test_laser_scan_all_zero_is_reported_in_detail() -> None:
    plugin = PointStreamPlugin()
    plugin.on_message("/scan", _laser_scan([0.0, 0.0, 0.0], range_min=0.0, range_max=10.0))
    status = plugin.get_status()
    assert status.severity == Severity.OK
    assert "SENS-003" in status.codes


def test_laser_scan_stuck_value_escalates_after_repeats() -> None:
    plugin = PointStreamPlugin()
    scan = _laser_scan([1.0, 2.0, 3.0])
    for _ in range(4):
        plugin.on_message("/scan", scan)
    status = plugin.get_status()
    assert status.severity == Severity.WARN
    assert "stuck for" in status.message
    assert status.values["stuck"] == "3"
    assert "SENS-002" in status.codes


def test_laser_scan_frame_id_change_is_flagged() -> None:
    plugin = PointStreamPlugin()
    plugin.on_message("/scan", _laser_scan([1.0], frame_id="laser_a"))
    plugin.on_message("/scan", _laser_scan([1.0], frame_id="laser_b"))
    status = plugin.get_status()
    assert status.severity == Severity.WARN
    assert "frame_id changed" in status.message


# --- PointCloud2 (3D) --------------------------------------------------------


def test_point_cloud_nominal_is_ok() -> None:
    plugin = PointStreamPlugin()
    plugin.on_message("/points", _point_cloud([(1.0, 2.0, 3.0), (4.0, 5.0, 6.0)]))
    status = plugin.get_status()
    assert status.severity == Severity.OK
    assert Text.from_markup(status.values["cloud"]).plain.startswith("2 pts  unorganized  dense ✓  fields x y z")
    assert status.topic_panel_value.endswith("2[/green]")


def test_point_cloud_nan_coordinate_counts_as_invalid() -> None:
    plugin = PointStreamPlugin()
    points = [(1.0, 2.0, 3.0)] * 9 + [(float("nan"), 0.0, 0.0)]  # 1/10 = 0.1 > default orange 0.05
    plugin.on_message("/points", _point_cloud(points))
    status = plugin.get_status()
    assert status.severity == Severity.ERROR
    assert "invalid ratio" in status.message


def test_point_cloud_nan_in_non_dense_cloud_is_a_no_return_not_invalid() -> None:
    """An organized lidar cloud (is_dense: false) keeps a NaN slot for every beam with no return."""
    plugin = PointStreamPlugin()
    points = [(1.0, 2.0, 3.0)] * 6 + [(float("nan"), float("nan"), float("nan"))] * 4
    plugin.on_message("/points", _point_cloud(points, is_dense=False))
    status = plugin.get_status()
    assert status.severity == Severity.OK
    assert _legend(status) == "60% valid · 40% no return · 0% invalid"


def test_point_cloud_inf_in_non_dense_cloud_is_still_invalid() -> None:
    plugin = PointStreamPlugin()
    points = [(1.0, 2.0, 3.0)] * 9 + [(float("inf"), 0.0, 0.0)]
    plugin.on_message("/points", _point_cloud(points, is_dense=False))
    status = plugin.get_status()
    assert status.severity == Severity.ERROR
    assert _legend(status) == "90% valid · 0% no return · 10% invalid"


def test_point_cloud_empty_payload_is_error() -> None:
    plugin = PointStreamPlugin()
    plugin.on_message("/points", _point_cloud([]))
    status = plugin.get_status()
    assert status.severity == Severity.ERROR
    assert "empty payload" in status.message


def test_point_cloud_truncated_data_is_malformed() -> None:
    plugin = PointStreamPlugin()
    msg = _point_cloud([(1.0, 2.0, 3.0), (4.0, 5.0, 6.0)])
    msg.data = msg.data[:10]
    plugin.on_message("/points", msg)
    status = plugin.get_status()
    assert status.severity == Severity.ERROR
    assert "shorter than height*row_step" in status.message


def test_point_cloud_stuck_cloud_escalates_after_repeats() -> None:
    plugin = PointStreamPlugin()
    cloud = _point_cloud([(1.0, 2.0, 3.0), (4.0, 5.0, 6.0)])
    for _ in range(4):
        plugin.on_message("/points", cloud)
    status = plugin.get_status()
    assert status.severity == Severity.WARN
    assert "stuck for" in status.message
    assert status.values["stuck"] == "3"


def test_point_cloud_frame_id_change_is_flagged() -> None:
    plugin = PointStreamPlugin()
    plugin.on_message("/points", _point_cloud([(1.0, 2.0, 3.0)], frame_id="lidar_a"))
    plugin.on_message("/points", _point_cloud([(1.0, 2.0, 3.0)], frame_id="lidar_b"))
    status = plugin.get_status()
    assert status.severity == Severity.WARN
    assert "frame_id changed" in status.message


def test_point_cloud_without_xyz_fields_reports_zero_invalid_ratio() -> None:
    """A cloud with no x/y/z fields (e.g. intensity-only) can't be NaN-checked -- reported as 0, not an error."""
    msg = PointCloud2()
    msg.header.frame_id = "lidar"
    msg.height = 1
    msg.width = 1
    msg.fields = [PointField(name="intensity", offset=0, datatype=PointField.FLOAT32, count=1)]
    msg.point_step = 4
    msg.row_step = 4
    msg.data = b"\x00\x00\x80\x7f"  # a single float32 NaN, in the one field this plugin doesn't check

    plugin = PointStreamPlugin()
    plugin.on_message("/points", msg)
    status = plugin.get_status()
    assert _legend(status) == "100% valid · 0% no return · 0% invalid"


def test_default_thresholds_cover_both_metrics() -> None:
    thresholds = PointStreamPlugin.default_thresholds()
    assert set(thresholds) == {"invalid_ratio", "stuck_count"}


def test_laser_scan_profile_and_beams() -> None:
    import math

    plugin = PointStreamPlugin()
    scan = _laser_scan([1.0, 2.0, float("inf"), float("nan")], range_max=4.0)
    scan.angle_min, scan.angle_max, scan.angle_increment = -math.pi / 2, math.pi / 2, math.radians(0.5)
    plugin.on_message("/scan", scan)
    values = plugin.get_status().values
    assert Text.from_markup(values["profile"]).plain == "▃▅█   -90° … +90°  0.00–4.0 m"
    assert values["beams"] == "4 @ 0.5°"


def test_returns_bar_is_fixed_width_and_never_hides_a_small_share() -> None:
    from testudo.plugins.builtin.point_stream_layout import format_points, returns_bar

    bar = Text.from_markup(returns_bar(0.99, 0.0, 0.0, 0.01)).plain
    assert len(bar) == 24 and bar.endswith("░")
    assert format_points(720) == "720" and format_points(29184) == "29.2k" and format_points(1_310_000) == "1.31M"
