"""Unit tests for GenericSensorPlugin -- synthetic LaserScan/Imu, no live ROS graph."""
from __future__ import annotations

from sensor_msgs.msg import Imu, LaserScan

from testudo.plugins.base import Severity
from testudo.plugins.builtin.sensors_generic import GenericSensorPlugin


def _laser_scan(ranges: list[float], range_min: float = 0.0, range_max: float = 10.0, frame_id: str = "laser") -> LaserScan:
    msg = LaserScan()
    msg.header.frame_id = frame_id
    msg.range_min = range_min
    msg.range_max = range_max
    msg.ranges = ranges
    return msg


def _imu(
    linear_accel: tuple[float, float, float] = (0.0, 0.0, 0.0),
    angular_vel: tuple[float, float, float] = (0.0, 0.0, 0.0),
    frame_id: str = "imu",
) -> Imu:
    msg = Imu()
    msg.header.frame_id = frame_id
    msg.linear_acceleration.x, msg.linear_acceleration.y, msg.linear_acceleration.z = linear_accel
    msg.angular_velocity.x, msg.angular_velocity.y, msg.angular_velocity.z = angular_vel
    return msg


def test_no_messages_yet_is_ok() -> None:
    plugin = GenericSensorPlugin()
    status = plugin.get_status()
    assert status.severity == Severity.OK
    assert "no messages received" in status.message


# --- LaserScan -----------------------------------------------------------


def test_laser_scan_nominal_is_ok() -> None:
    plugin = GenericSensorPlugin()
    plugin.on_message("/scan", _laser_scan([1.0, 2.0, 3.0]))
    assert plugin.get_status().severity == Severity.OK


def test_laser_scan_nan_counts_as_invalid() -> None:
    plugin = GenericSensorPlugin()
    ranges = [1.0] * 9 + [float("nan")]  # 1/10 = 0.1 > default orange 0.05
    plugin.on_message("/scan", _laser_scan(ranges))
    status = plugin.get_status()
    assert status.severity == Severity.ERROR
    assert "invalid ratio" in status.message


def test_laser_scan_positive_inf_is_valid_per_rep117() -> None:
    plugin = GenericSensorPlugin()
    ranges = [1.0] * 9 + [float("inf")]
    plugin.on_message("/scan", _laser_scan(ranges))
    status = plugin.get_status()
    assert status.severity == Severity.OK
    assert status.values["invalid_ratio"] == "0"


def test_laser_scan_negative_inf_counts_as_invalid() -> None:
    plugin = GenericSensorPlugin()
    ranges = [1.0] * 9 + [float("-inf")]
    plugin.on_message("/scan", _laser_scan(ranges))
    status = plugin.get_status()
    assert status.severity == Severity.ERROR


def test_laser_scan_out_of_bounds_value_flags_plausibility() -> None:
    plugin = GenericSensorPlugin()
    plugin.on_message("/scan", _laser_scan([1.0, 2.0, 50.0], range_min=0.0, range_max=10.0))
    status = plugin.get_status()
    assert status.severity != Severity.OK
    assert "outside plausible range" in status.message


def test_laser_scan_all_zero_is_reported_in_detail() -> None:
    plugin = GenericSensorPlugin()
    plugin.on_message("/scan", _laser_scan([0.0, 0.0, 0.0], range_min=0.0, range_max=10.0))
    status = plugin.get_status()
    assert status.severity == Severity.OK
    assert status.values["detail"] == "all-zero scan"


def test_laser_scan_stuck_value_escalates_after_repeats() -> None:
    plugin = GenericSensorPlugin()
    scan = _laser_scan([1.0, 2.0, 3.0])
    for _ in range(4):
        plugin.on_message("/scan", scan)
    status = plugin.get_status()
    assert status.severity == Severity.WARN
    assert "stuck for" in status.message
    assert status.values["stuck_count"] == "3"


def test_laser_scan_frame_id_change_is_flagged() -> None:
    plugin = GenericSensorPlugin()
    plugin.on_message("/scan", _laser_scan([1.0], frame_id="laser_a"))
    plugin.on_message("/scan", _laser_scan([1.0], frame_id="laser_b"))
    status = plugin.get_status()
    assert status.severity == Severity.WARN
    assert "frame_id changed" in status.message


# --- Imu -------------------------------------------------------------------


def test_imu_nominal_is_ok() -> None:
    plugin = GenericSensorPlugin()
    plugin.on_message("/imu", _imu(linear_accel=(0.0, 0.0, 9.81)))
    assert plugin.get_status().severity == Severity.OK


def test_imu_nan_field_counts_as_invalid() -> None:
    plugin = GenericSensorPlugin()
    plugin.on_message("/imu", _imu(linear_accel=(float("nan"), 0.0, 9.81)))
    status = plugin.get_status()
    assert status.severity == Severity.ERROR


def test_imu_implausible_acceleration_is_flagged() -> None:
    plugin = GenericSensorPlugin()
    plugin.on_message("/imu", _imu(linear_accel=(500.0, 0.0, 0.0)))
    status = plugin.get_status()
    assert status.severity != Severity.OK
    assert "outside plausible range" in status.message


def test_imu_stuck_value_escalates_after_repeats() -> None:
    plugin = GenericSensorPlugin()
    reading = _imu(linear_accel=(1.0, 2.0, 3.0), angular_vel=(0.1, 0.2, 0.3))
    for _ in range(4):
        plugin.on_message("/imu", reading)
    status = plugin.get_status()
    assert status.severity == Severity.WARN
    assert status.values["stuck_count"] == "3"


def test_default_thresholds_cover_both_metrics() -> None:
    thresholds = GenericSensorPlugin.default_thresholds()
    assert set(thresholds) == {"invalid_ratio", "stuck_count"}
