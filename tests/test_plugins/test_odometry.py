"""Unit tests for OdometryPlugin -- synthetic nav_msgs/Odometry (+ Twist), no live ROS graph."""
from __future__ import annotations

import math

import pytest
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry

from rich.text import Text

from testudo.plugins.base import Severity
from testudo.plugins.builtin.odometry import OdometryPlugin


def _plain(markup: str) -> str:
    return Text.from_markup(markup).plain


def _odometry(
    *,
    stamp_sec: int = 0,
    stamp_nanosec: int = 0,
    xx: float = 0.0,
    yy: float = 0.0,
    zz: float = 0.0,
    yaw_variance: float = 0.0,
    linear_x: float = 0.0,
    angular_z: float = 0.0,
) -> Odometry:
    msg = Odometry()
    msg.header.stamp.sec = stamp_sec
    msg.header.stamp.nanosec = stamp_nanosec
    cov = [0.0] * 36
    cov[0], cov[7], cov[14] = xx, yy, zz
    cov[35] = yaw_variance
    msg.pose.covariance = cov
    msg.twist.covariance = [0.0] * 36
    msg.twist.twist.linear.x = linear_x
    msg.twist.twist.angular.z = angular_z
    return msg


def _twist(linear_x: float = 0.0, angular_z: float = 0.0) -> Twist:
    msg = Twist()
    msg.linear.x = linear_x
    msg.angular.z = angular_z
    return msg


def test_no_messages_yet_is_ok() -> None:
    plugin = OdometryPlugin()
    status = plugin.get_status()
    assert status.severity == Severity.OK
    assert "no odometry received" in status.message


def test_nominal_zero_covariance_is_ok() -> None:
    plugin = OdometryPlugin()
    plugin.on_message("/odom", _odometry())
    status = plugin.get_status()
    assert status.severity == Severity.OK


def test_position_covariance_trace_beyond_orange_is_error() -> None:
    plugin = OdometryPlugin()
    plugin.on_message("/odom", _odometry(xx=0.2, yy=0.2, zz=0.2))  # trace 0.6 >> default orange 0.1
    status = plugin.get_status()
    assert status.severity == Severity.ERROR
    assert "position covariance trace" in status.message


def test_yaw_variance_beyond_orange_is_error() -> None:
    plugin = OdometryPlugin()
    plugin.on_message("/odom", _odometry(yaw_variance=0.5))
    status = plugin.get_status()
    assert status.severity == Severity.ERROR
    assert "yaw variance" in status.message


def test_negative_diagonal_covariance_fails_psd_check() -> None:
    plugin = OdometryPlugin()
    msg = _odometry()
    cov = list(msg.pose.covariance)
    cov[0] = -1.0  # a real covariance matrix can never have a negative variance
    msg.pose.covariance = cov
    plugin.on_message("/odom", msg)
    status = plugin.get_status()
    assert status.severity == Severity.ERROR
    assert "positive semi-definite" in status.message
    axes = _plain(status.values["axes"])
    assert "✗ pose" in axes
    assert "✓ twist" in axes
    assert set(status.codes) == {"ODOM-004"}  # twist covariance is untouched (all zero, valid PSD)


def test_rapid_covariance_growth_is_flagged() -> None:
    plugin = OdometryPlugin()
    plugin.on_message("/odom", _odometry(stamp_sec=0, xx=0.0))
    plugin.on_message("/odom", _odometry(stamp_sec=0, stamp_nanosec=10_000_000, xx=0.05))  # +0.05 in 10ms = 5/s
    status = plugin.get_status()
    assert status.severity == Severity.ERROR
    assert "covariance growing" in status.message


def test_cmd_vel_cross_check_without_related_topic_configured_is_ok() -> None:
    plugin = OdometryPlugin()
    plugin.on_message("/odom", _odometry(linear_x=0.0))
    status = plugin.get_status()
    assert "cmd     – m/s" in _plain(status.values["axes"])
    assert status.severity == Severity.OK


def test_cmd_vel_cross_check_flags_commanded_motion_with_no_odometry_movement() -> None:
    plugin = OdometryPlugin(related_topics={"cmd_vel": "/cmd_vel"})
    plugin.on_message("/cmd_vel", _twist(linear_x=0.5))
    plugin.on_message("/odom", _odometry(linear_x=0.0, angular_z=0.0))
    status = plugin.get_status()
    assert status.severity == Severity.WARN
    assert "cmd_vel commands motion" in status.message
    assert "0.00  cmd  0.50 m/s" in _plain(status.values["axes"])
    assert "ODOM-006" in status.codes


def test_cmd_vel_cross_check_passes_when_odometry_matches_commanded_motion() -> None:
    plugin = OdometryPlugin(related_topics={"cmd_vel": "/cmd_vel"})
    plugin.on_message("/cmd_vel", _twist(linear_x=0.5))
    plugin.on_message("/odom", _odometry(linear_x=0.5))
    status = plugin.get_status()
    assert status.severity == Severity.OK


def test_cmd_vel_message_routes_by_topic_name_not_message_type() -> None:
    plugin = OdometryPlugin(related_topics={"cmd_vel": "/cmd_vel"})
    plugin.on_message("/cmd_vel", _twist(linear_x=1.0))
    # No odometry message yet -- only the cmd_vel side has been seen.
    assert plugin.get_status().message == "no odometry received yet"


def test_default_thresholds_cover_all_odometry_metrics() -> None:
    thresholds = OdometryPlugin.default_thresholds()
    assert set(thresholds) == {"position_covariance_trace", "yaw_variance", "covariance_growth_rate"}


def test_no_messages_yet_has_no_topic_panel_column() -> None:
    # Unreachable via the live TUI (liveness always pre-empts this status
    # first -- see TUI_DATA_DESIGN.md), but still correct in isolation.
    plugin = OdometryPlugin()
    status = plugin.get_status()
    assert status.topic_panel_column == ""
    assert status.topic_panel_value == ""


def test_topic_panel_column_is_horizontal_sigma() -> None:
    plugin = OdometryPlugin()
    plugin.on_message("/odom", _odometry(xx=0.03, yy=0.0076))
    status = plugin.get_status()
    assert status.topic_panel_column == "σxy"
    assert _plain(status.topic_panel_value) == "±0.19"


def test_topic_panel_value_is_colored_by_its_own_zone_with_a_growth_arrow() -> None:
    plugin = OdometryPlugin()
    plugin.on_message("/odom", _odometry(stamp_sec=0, xx=0.0))
    plugin.on_message("/odom", _odometry(stamp_sec=1, xx=0.2, yy=0.2, zz=0.2))  # trace 0.6 >> orange 0.1, growing
    status = plugin.get_status()
    assert "[bold red]" in status.topic_panel_value
    assert _plain(status.topic_panel_value).endswith("↑")


def test_growth_rate_is_shown_with_a_meter_colored_by_its_zone() -> None:
    plugin = OdometryPlugin()
    plugin.on_message("/odom", _odometry(stamp_sec=0, xx=0.0))
    plugin.on_message("/odom", _odometry(stamp_sec=0, stamp_nanosec=10_000_000, xx=0.05))  # +0.05 in 10ms = 5/s -> ERROR
    status = plugin.get_status()
    assert "growth +5.000 m²/s" in _plain(status.values["σxy"])
    assert "[bold red]▮" in status.values["σxy"]
    assert "ODOM-003" in status.codes


def test_axes_block_has_a_row_per_axis_with_sigma_and_motion_side_by_side() -> None:
    plugin = OdometryPlugin()
    plugin.on_message("/odom", _odometry(xx=0.0676, yaw_variance=0.0001))
    rows = _plain(plugin.get_status().values["axes"]).splitlines()
    assert len(rows) == 3
    assert rows[0].startswith("x      ±0.26m ")
    assert "roll" in rows[0] and rows[0].rstrip().endswith("m/s")
    assert "yaw    ±0.57°" in rows[2] and "PSD" in rows[2]
    # The orientation group starts in the same column on every row.
    assert len({row.index(axis) for row, axis in zip(rows, ("roll", "pitch", "yaw"))}) == 1


def test_axes_compact_variant_stacks_motion_under_one_square_meters() -> None:
    plugin = OdometryPlugin()
    plugin.on_message("/odom", _odometry(xx=0.2))
    status = plugin.get_status()
    compact = _plain(status.values["axes_compact"]).splitlines()
    assert len(compact) == 6
    assert compact[0].count("▮") + compact[0].count("▯") == 2  # one square each for x and roll


def test_sigma_xy_trend_samples_once_a_second_on_tick() -> None:
    plugin = OdometryPlugin()
    plugin.on_tick(1.0)
    plugin.on_message("/odom", _odometry(xx=0.01))
    for t in (1.0, 1.5, 2.0, 3.0):
        plugin.on_tick(t)
    plugin.on_message("/odom", _odometry(xx=0.04))
    plugin.on_tick(4.0)
    value = _plain(plugin.get_status().values["σxy"])
    assert value.startswith("▅▅▅█ (4 s)")  # σ 0.1 then 0.2, scaled from 0


def test_frame_pair_and_secondary_values() -> None:
    plugin = OdometryPlugin()
    msg = _odometry(stamp_sec=9)
    msg.header.frame_id = "odom"
    msg.child_frame_id = "base_link"
    msg.pose.pose.position.x = 1.234
    plugin.on_tick(9.012)
    plugin.on_message("/odom", msg)
    values = plugin.get_status().values
    assert _plain(values["σxy"]).endswith("odom → base_link")
    assert "x 1.234" in _plain(values["pose_more"])
    assert values["stamp_lag_more"] == "12 ms"
    assert "twist_σ_more" in values


def test_codes_go_in_the_generic_code_lines() -> None:
    plugin = OdometryPlugin(related_topics={"cmd_vel": "/cmd_vel"})
    plugin.on_message("/cmd_vel", _twist(linear_x=0.5))
    plugin.on_message("/odom", _odometry(xx=0.2, yy=0.2, zz=0.2, linear_x=0.0))  # trace over + cmd_vel mismatch
    status = plugin.get_status()
    assert set(status.codes) == {"ODOM-001", "ODOM-006"}
    assert "[bold red]" in status.codes["ODOM-001"]


def test_quaternion_to_euler_recovers_a_quarter_turn_yaw() -> None:
    from testudo.plugins.builtin.odometry import _quaternion_to_euler

    half_sqrt2 = 0.5**0.5
    roll, pitch, yaw = _quaternion_to_euler(x=0.0, y=0.0, z=half_sqrt2, w=half_sqrt2)  # 90 deg about Z
    assert roll == pytest.approx(0.0, abs=1e-9)
    assert pitch == pytest.approx(0.0, abs=1e-9)
    assert yaw == pytest.approx(math.pi / 2, abs=1e-9)


def test_split_zone_divides_every_configured_boundary() -> None:
    from testudo.plugins.base import ThresholdZone
    from testudo.plugins.builtin.odometry import _split_zone

    zone = _split_zone(ThresholdZone(green=0.03, orange=0.3), 3)
    assert zone.green == pytest.approx(0.01)
    assert zone.orange == pytest.approx(0.1)
    assert zone.red is None
    assert _split_zone(None, 3) is None
