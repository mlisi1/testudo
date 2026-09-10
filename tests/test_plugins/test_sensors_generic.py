"""Unit tests for GenericSensorPlugin -- synthetic Imu, no live ROS graph."""
from __future__ import annotations

from sensor_msgs.msg import Imu

from testudo.plugins.base import Severity
from testudo.plugins.builtin.sensors_generic import GenericSensorPlugin


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


def test_msg_types_is_imu_only() -> None:
    assert GenericSensorPlugin.msg_types() == ("sensor_msgs/msg/Imu",)
