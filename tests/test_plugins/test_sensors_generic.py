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
    assert "SENS-004" in status.codes


def test_imu_stuck_value_escalates_after_repeats() -> None:
    plugin = GenericSensorPlugin()
    reading = _imu(linear_accel=(1.0, 2.0, 3.0), angular_vel=(0.1, 0.2, 0.3))
    for _ in range(4):
        plugin.on_message("/imu", reading)
    status = plugin.get_status()
    assert status.severity == Severity.WARN
    assert status.values["stuck"] == "3"
    assert "SENS-002" in status.codes


def test_default_thresholds_cover_both_metrics() -> None:
    thresholds = GenericSensorPlugin.default_thresholds()
    assert set(thresholds) == {"invalid_ratio", "stuck_count"}


def test_msg_types_is_imu_only() -> None:
    assert GenericSensorPlugin.msg_types() == ("sensor_msgs/msg/Imu",)


def test_imu_panel_shows_vectors_magnitude_and_covariance_fill() -> None:
    from rich.text import Text

    plugin = GenericSensorPlugin()
    msg = _imu(linear_accel=(0.12, -0.08, 9.78), angular_vel=(0.003, -0.001, 0.004))
    msg.orientation_covariance = [-1.0] + [0.0] * 8
    msg.angular_velocity_covariance = [0.01, 0, 0, 0, 0.01, 0, 0, 0, 0.01]
    plugin.on_message("/imu", msg)
    status = plugin.get_status()
    plain = {key: Text.from_markup(value).plain for key, value in status.values.items()}
    assert plain["accel"] == "x   0.12  y  -0.08  z   9.78   |a| 9.78 m/s²"
    assert plain["covariance"] == "orient not provided · gyro ✓ · accel all zero"
    assert plain["gravity_on_more"] == "+z"
    assert status.topic_panel_column == "|a|"
    assert status.topic_panel_value == "9.78"


def test_imu_gyro_trend_samples_once_a_second() -> None:
    plugin = GenericSensorPlugin()
    plugin.on_message("/imu", _imu(angular_vel=(0.0, 0.0, 0.5)))
    for t in (1.0, 1.4, 2.0):
        plugin.on_tick(t)
    assert plugin.get_status().values["|ω|"].endswith("(2 s)[/dim]")
