"""Unit tests for OdometryPlugin -- synthetic nav_msgs/Odometry (+ Twist), no live ROS graph."""
from __future__ import annotations

from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry

from testudo.plugins.base import Severity
from testudo.plugins.builtin.odometry import OdometryPlugin


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
    assert status.values["pose_psd_ok"] == "False"


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
    assert status.values["cmd_vel_check"] == "no cmd_vel received yet"
    assert status.severity == Severity.OK


def test_cmd_vel_cross_check_flags_commanded_motion_with_no_odometry_movement() -> None:
    plugin = OdometryPlugin(related_topics={"cmd_vel": "/cmd_vel"})
    plugin.on_message("/cmd_vel", _twist(linear_x=0.5))
    plugin.on_message("/odom", _odometry(linear_x=0.0, angular_z=0.0))
    status = plugin.get_status()
    assert status.severity == Severity.WARN
    assert "cmd_vel commands motion" in status.message


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
