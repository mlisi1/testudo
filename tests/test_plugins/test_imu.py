"""Unit tests for ImuPlugin -- synthetic Imu, no live ROS graph."""
from __future__ import annotations

import pytest
from nav_msgs.msg import Odometry
from rich.text import Text
from sensor_msgs.msg import Imu

from testudo.plugins.base import Severity
from testudo.plugins.builtin.imu import ImuPlugin


def _plain(markup: str) -> str:
    return Text.from_markup(markup).plain


def _imu(
    linear_accel: tuple[float, float, float] = (0.0, 0.0, 0.0),
    angular_vel: tuple[float, float, float] = (0.0, 0.0, 0.0),
    frame_id: str = "imu",
) -> Imu:
    msg = Imu()
    msg.header.frame_id = frame_id
    msg.linear_acceleration.x, msg.linear_acceleration.y, msg.linear_acceleration.z = linear_accel
    msg.angular_velocity.x, msg.angular_velocity.y, msg.angular_velocity.z = angular_vel
    msg.orientation_covariance = [-1.0] + [0.0] * 8
    msg.angular_velocity_covariance = [0.01, 0.0, 0.0, 0.0, 0.01, 0.0, 0.0, 0.0, 0.01]
    msg.linear_acceleration_covariance = [0.01, 0.0, 0.0, 0.0, 0.01, 0.0, 0.0, 0.0, 0.01]
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
    assert set(status.codes) == {"IMU-001"}


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
    msg.linear_acceleration_covariance = [0.0] * 9
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


def test_a_gyro_only_stream_is_not_read_as_gravity_removed() -> None:
    # A RealSense without unite_imu_method (field bag, 2026-11-23): gyro/sample
    # leaves linear_acceleration at zero in every message.
    plugin = ImuPlugin()
    for step in range(30):
        plugin.on_tick(100.0 + step * 0.1)
        plugin.on_message("/gyro", _imu(angular_vel=(0.001 * (step % 2), -0.003, 0.0)))
    status = plugin.get_status()
    assert status.codes == {}
    assert "gravity" not in status.values and "gravity" not in status.metrics
    assert "gravity_on_more" not in status.values
    assert _plain(status.values["accel"]) == "always 0: not in this stream"
    assert _plain(status.topic_panel_value) == "–"


def test_an_accel_only_stream_still_judges_gravity() -> None:
    plugin = ImuPlugin()
    for step in range(30):
        plugin.on_tick(100.0 + step * 0.1)
        plugin.on_message("/accel", _imu(linear_accel=(0.18 + 0.001 * (step % 2), -9.69, 0.47)))
    status = plugin.get_status()
    assert status.metrics["gravity"] == "included"
    assert _plain(status.values["gyro"]) == "always 0: not in this stream"


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


def _oriented(x: float, y: float, z: float, w: float) -> Imu:
    msg = _imu(linear_accel=(0.0, 0.0, 9.81))
    msg.orientation.x, msg.orientation.y, msg.orientation.z, msg.orientation.w = x, y, z, w
    msg.orientation_covariance = [0.01, 0.0, 0.0, 0.0, 0.01, 0.0, 0.0, 0.0, 0.01]
    return msg


def _status_of(*messages: Imu) -> object:
    plugin = ImuPlugin()
    for message in messages:
        plugin.on_message("/imu", message)
    return plugin.get_status()


def test_a_unit_quaternion_is_fine() -> None:
    assert _status_of(_oriented(0.0, 0.0, 0.0, 1.0)).codes == {}


def test_a_slightly_denormalized_quaternion_warns_and_a_broken_one_errors() -> None:
    assert _status_of(_oriented(0.0, 0.0, 0.0, 1.05)).code_severities == {"IMU-007": Severity.WARN}
    assert _status_of(_oriented(0.0, 0.0, 0.0, 2.0)).code_severities == {"IMU-007": Severity.ERROR}


def test_the_rep_145_sentinel_means_no_orientation_to_judge() -> None:
    msg = _imu(linear_accel=(0.0, 0.0, 9.81))  # zero quaternion, covariance[0] == -1
    assert _status_of(msg).codes == {}


def test_a_covariance_left_at_zero_is_a_placeholder() -> None:
    msg = _imu(linear_accel=(0.0, 0.0, 9.81))
    msg.angular_velocity_covariance = [0.0] * 9
    status = _status_of(msg)
    assert status.code_severities == {"IMU-008": Severity.WARN}
    assert "gyro covariance all zero" in status.codes["IMU-008"]


def test_an_unfilled_orientation_is_only_reported_as_a_placeholder() -> None:
    msg = _imu(linear_accel=(0.0, 0.0, 9.81))
    msg.orientation_covariance = [0.0] * 9
    assert set(_status_of(msg).codes) == {"IMU-008"}


def _clipped(step: int, value: float = 39.2) -> Imu:
    return _imu(linear_accel=(value, 0.1 * step, 9.81), angular_vel=(0.01 * step, 0.0, 0.0))


def test_an_axis_pinned_at_a_large_value_is_clipping() -> None:
    plugin = ImuPlugin()
    for step in range(5):
        plugin.on_tick(100.0 + step * 0.1)
        plugin.on_message("/imu", _clipped(step))
    status = plugin.get_status()
    assert status.code_severities == {"IMU-009": Severity.WARN}
    assert "accel x clipped at 39.2" in status.codes["IMU-009"]


def test_clipping_stays_active_for_a_while_then_clears() -> None:
    plugin = ImuPlugin()
    for step in range(5):
        plugin.on_tick(100.0 + step * 0.1)
        plugin.on_message("/imu", _clipped(step))
    plugin.on_tick(100.4 + 10.0)
    assert "IMU-009" not in plugin.get_status().codes


def test_a_small_repeating_value_is_not_clipping() -> None:
    assert "IMU-009" not in _status_of(*(_clipped(step, value=0.5) for step in range(6))).codes


def test_a_frozen_imu_is_stuck_rather_than_clipping() -> None:
    frozen = _clipped(1)
    assert set(_status_of(*(frozen for _ in range(6))).codes) == {"IMU-002"}


def _odom(linear_x: float = 0.0) -> Odometry:
    msg = Odometry()
    msg.twist.twist.linear.x = linear_x
    return msg


def _still_robot(gyro_z: float, odom_speed: float = 0.0) -> ImuPlugin:
    plugin = ImuPlugin(related_topics={"odom": "/odom"})
    for step in range(40):
        plugin.on_tick(100.0 + step * 0.1)
        plugin.on_message("/odom", _odom(odom_speed))
        plugin.on_message("/imu", _imu(linear_accel=(0.0, 0.0, 9.81 + 0.001 * (step % 2)), angular_vel=(0.0, 0.0, gyro_z)))
    return plugin


def test_a_gyro_drifting_at_rest_is_biased() -> None:
    status = _still_robot(gyro_z=0.05).get_status()
    assert status.code_severities == {"IMU-010": Severity.WARN}
    assert status.metrics["gyro_bias_rad_per_s"] == pytest.approx(0.05)


def test_a_small_gyro_offset_is_fine() -> None:
    assert _still_robot(gyro_z=0.005).get_status().codes == {}


def test_a_robot_on_the_move_says_nothing_about_bias() -> None:
    status = _still_robot(gyro_z=0.5, odom_speed=0.5).get_status()
    assert "IMU-010" not in status.codes
    assert "gyro_bias_rad_per_s" not in status.metrics


def test_without_odometry_a_steady_turn_is_not_called_bias() -> None:
    plugin = ImuPlugin()
    for step in range(40):
        plugin.on_tick(100.0 + step * 0.1)
        plugin.on_message("/imu", _imu(linear_accel=(0.0, 0.0, 9.81 + 0.001 * (step % 2)), angular_vel=(0.0, 0.0, 0.5)))
    assert "IMU-010" not in plugin.get_status().codes


def _stamped(seconds: float, step: int) -> Imu:
    msg = _imu(linear_accel=(0.0, 0.0, 9.81 + 0.001 * (step % 2)))
    msg.header.stamp.sec = int(seconds)
    msg.header.stamp.nanosec = int((seconds % 1) * 1e9)
    return msg


def _stamp_plugin(stamps: list[float]) -> ImuPlugin:
    plugin = ImuPlugin()
    plugin.on_tick(stamps[-1])
    for step, stamp in enumerate(stamps):
        plugin.on_message("/imu", _stamped(stamp, step))
    return plugin


def test_regular_stamps_are_fine() -> None:
    assert _stamp_plugin([100.0 + step * 0.1 for step in range(20)]).get_status().codes == {}


def test_a_gap_between_stamps_is_flagged() -> None:
    stamps = [100.0 + step * 0.1 for step in range(10)] + [101.5]
    status = _stamp_plugin(stamps).get_status()
    assert status.code_severities == {"IMU-011": Severity.WARN}
    assert "between stamps" in status.codes["IMU-011"]


def test_a_stamp_going_backwards_is_flagged() -> None:
    stamps = [100.0 + step * 0.1 for step in range(5)] + [100.1]
    assert "went back" in _stamp_plugin(stamps).get_status().codes["IMU-011"]


def test_sustained_jitter_is_flagged() -> None:
    stamps, stamp = [], 100.0
    for step in range(20):
        stamp += (0.02, 0.1, 0.18)[step % 3]  # irregular, but no interval far from the usual one
        stamps.append(stamp)
    assert "jitter" in _stamp_plugin(stamps).get_status().codes["IMU-011"]

