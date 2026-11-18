"""Unit tests for ImuPlugin -- synthetic Imu, no live ROS graph."""
from __future__ import annotations

import pytest
from sensor_msgs.msg import Imu

from testudo.plugins.base import Severity
from testudo.plugins.builtin.imu import ImuPlugin


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
    plugin = ImuPlugin()
    status = plugin.get_status()
    assert status.severity == Severity.OK
    assert "no messages received" in status.message


def test_imu_nominal_is_ok() -> None:
    plugin = ImuPlugin()
    plugin.on_message("/imu", _imu(linear_accel=(0.0, 0.0, 9.81)))
    assert plugin.get_status().severity == Severity.OK


def test_imu_nan_field_counts_as_invalid() -> None:
    plugin = ImuPlugin()
    plugin.on_message("/imu", _imu(linear_accel=(float("nan"), 0.0, 9.81)))
    status = plugin.get_status()
    assert status.severity == Severity.ERROR
    assert "IMU-001" in status.codes


def test_imu_implausible_acceleration_is_flagged() -> None:
    plugin = ImuPlugin()
    plugin.on_message("/imu", _imu(linear_accel=(500.0, 0.0, 0.0)))
    status = plugin.get_status()
    assert status.severity != Severity.OK
    assert "outside plausible range" in status.message
    assert "IMU-003" in status.codes


def test_imu_stuck_value_escalates_after_repeats() -> None:
    plugin = ImuPlugin()
    reading = _imu(linear_accel=(1.0, 2.0, 3.0), angular_vel=(0.1, 0.2, 0.3))
    for _ in range(4):
        plugin.on_message("/imu", reading)
    status = plugin.get_status()
    assert status.severity == Severity.WARN
    assert status.values["stuck"] == "3"
    assert "IMU-002" in status.codes


def test_default_thresholds_cover_both_metrics() -> None:
    thresholds = ImuPlugin.default_thresholds()
    assert set(thresholds) == {"invalid_ratio", "stuck_count"}


def test_msg_types_is_imu_only() -> None:
    assert ImuPlugin.msg_types() == ("sensor_msgs/msg/Imu",)


def test_imu_panel_shows_vectors_magnitude_and_covariance_fill() -> None:
    from rich.text import Text

    plugin = ImuPlugin()
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
    plugin = ImuPlugin()
    plugin.on_message("/imu", _imu(angular_vel=(0.0, 0.0, 0.5)))
    for t in (1.0, 1.4, 2.0):
        plugin.on_tick(t)
    assert plugin.get_status().values["|ω|"].endswith("(2 s)[/dim]")


def test_metrics_publish_per_axis_readings_and_magnitudes() -> None:
    plugin = ImuPlugin()
    plugin.on_message("/imu", _imu(linear_accel=(0.0, 0.0, 9.81), angular_vel=(0.0, 0.0, 0.5)))
    metrics = plugin.get_status().metrics
    assert metrics["accel_z_m_per_s2"] == 9.81
    assert metrics["accel_magnitude_m_per_s2"] == 9.81
    assert metrics["gyro_magnitude_rad_per_s"] == 0.5
    assert metrics["frame_id"] == "imu"


def _at_rest(plugin: ImuPlugin, accel: tuple[float, float, float], start: float = 100.0, seconds: float = 3.0) -> float:
    """Feed a still IMU at 10 Hz, with a little noise so it never reads as stuck; the time reached."""
    steps = int(seconds * 10)
    for step in range(steps):
        now = start + step * 0.1
        plugin.on_tick(now)
        wobble = 0.001 * (step % 2)
        plugin.on_message("/imu", _imu(linear_accel=(accel[0] + wobble, accel[1], accel[2]), angular_vel=(wobble, 0.0, 0.0)))
    return start + steps * 0.1


@pytest.mark.parametrize(
    "accel",
    [(0.0, 0.0, 9.81), (9.81, 0.0, 0.0), (0.0, -9.81, 0.0), (5.664, 5.664, 5.664)],
    ids=["flat", "sideways", "upside-along-y", "tilted"],
)
def test_gravity_is_fine_on_any_axis(accel: tuple[float, float, float]) -> None:
    plugin = ImuPlugin()
    _at_rest(plugin, accel)
    status = plugin.get_status()
    assert status.codes == {}
    assert status.metrics["gravity"] == "included"


def test_gravity_already_removed_is_fine() -> None:
    plugin = ImuPlugin()
    _at_rest(plugin, (0.01, 0.0, 0.02))
    status = plugin.get_status()
    assert status.codes == {}
    assert status.metrics["gravity"] == "removed"


def test_an_imu_reporting_in_g_is_flagged() -> None:
    plugin = ImuPlugin()
    _at_rest(plugin, (0.0, 0.0, 1.0))
    status = plugin.get_status()
    assert status.code_severities == {"IMU-005": Severity.WARN}
    assert "in g" in status.codes["IMU-005"]


def test_an_acceleration_of_the_wrong_size_is_flagged() -> None:
    plugin = ImuPlugin()
    _at_rest(plugin, (0.0, 0.0, 7.5))
    assert "expected 9.81 or 0" in plugin.get_status().codes["IMU-005"]


def test_acceleration_is_not_judged_while_turning() -> None:
    plugin = ImuPlugin()
    for step in range(40):
        plugin.on_tick(100.0 + step * 0.1)
        plugin.on_message("/imu", _imu(linear_accel=(0.0, 0.0, 7.5 + 0.001 * (step % 2)), angular_vel=(0.0, 0.0, 0.5)))
    assert plugin.get_status().codes == {}


def test_acceleration_is_not_judged_while_shaking() -> None:
    plugin = ImuPlugin()
    for step in range(40):
        plugin.on_tick(100.0 + step * 0.1)
        plugin.on_message("/imu", _imu(linear_accel=(0.0, 0.0, 7.5 + 2.0 * (step % 2))))
    assert plugin.get_status().codes == {}


def test_too_short_a_rest_says_nothing() -> None:
    plugin = ImuPlugin()
    _at_rest(plugin, (0.0, 0.0, 1.0), seconds=1.0)
    assert plugin.get_status().codes == {}


def test_a_verdict_survives_the_robot_moving_off() -> None:
    plugin = ImuPlugin()
    now = _at_rest(plugin, (0.0, 0.0, 1.0))
    plugin.on_tick(now)
    plugin.on_message("/imu", _imu(linear_accel=(3.0, 0.0, 1.0), angular_vel=(0.0, 0.0, 1.0)))
    assert "IMU-005" in plugin.get_status().codes


def test_gravity_appearing_mid_session_is_flagged_for_a_while() -> None:
    plugin = ImuPlugin()
    now = _at_rest(plugin, (0.0, 0.0, 9.81))
    now = _at_rest(plugin, (0.0, 0.0, 0.0), start=now)
    status = plugin.get_status()
    assert status.code_severities == {"IMU-006": Severity.WARN}
    assert "included to removed" in status.codes["IMU-006"]
    plugin.on_tick(now + 11.0)
    assert plugin.get_status().codes == {}
