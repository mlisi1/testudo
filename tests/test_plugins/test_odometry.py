"""Unit tests for OdometryPlugin -- synthetic nav_msgs/Odometry (+ Twist), no live ROS graph."""
from __future__ import annotations

import math
import re

import pytest
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
    assert "pose✗" in status.values["growth_psd"]
    assert "twist✓" in status.values["growth_psd"]


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
    assert "cmd_vel: no cmd_vel received yet" in status.values["growth_psd"]
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


def test_no_messages_yet_has_no_topic_panel_column() -> None:
    # Unreachable via the live TUI (liveness always pre-empts this status
    # first -- see TUI_DATA_DESIGN.md), but still correct in isolation.
    plugin = OdometryPlugin()
    status = plugin.get_status()
    assert status.topic_panel_column == ""
    assert status.topic_panel_value == ""


def test_topic_panel_column_is_covariance_trace() -> None:
    plugin = OdometryPlugin()
    plugin.on_message("/odom", _odometry(xx=0.03))
    status = plugin.get_status()
    assert status.topic_panel_column == "Cov"
    assert "0.03" in status.topic_panel_value


def test_topic_panel_value_is_colored_by_its_own_zone() -> None:
    plugin = OdometryPlugin()
    plugin.on_message("/odom", _odometry(xx=0.2, yy=0.2, zz=0.2))  # trace 0.6 >> default orange 0.1 -> ERROR
    status = plugin.get_status()
    assert "[bold red]" in status.topic_panel_value


def test_growth_psd_detail_reflects_severity_and_psd_state() -> None:
    plugin = OdometryPlugin()
    plugin.on_message("/odom", _odometry(stamp_sec=0, xx=0.0))
    plugin.on_message("/odom", _odometry(stamp_sec=0, stamp_nanosec=10_000_000, xx=0.05))  # +0.05 in 10ms = 5/s -> ERROR
    status = plugin.get_status()
    detail = status.values["growth_psd"]
    assert "pose✓" in detail
    assert "twist✓" in detail
    assert "[bold red]" in detail  # growth rate meter colored by its own (ERROR) zone


def test_position_orientation_block_has_every_axis_labeled() -> None:
    plugin = OdometryPlugin()
    plugin.on_message("/odom", _odometry(xx=0.2, yaw_variance=0.05))
    status = plugin.get_status()
    block = status.values["position_orientation"]
    for axis in ("x", "y", "z", "roll", "pitch", "yaw"):
        assert axis in block
    assert block.count("\n") == 4  # header row + 3 side-by-side rows + trailing blank-line separator


def test_position_orientation_block_has_value_and_cov_column_headers() -> None:
    plugin = OdometryPlugin()
    plugin.on_message("/odom", _odometry(xx=0.2))
    status = plugin.get_status()
    header = status.values["position_orientation"].splitlines()[0]
    assert header.count("value") == 2  # one per side-by-side group
    assert header.count("cov") == 2
    assert header.count("[b]") == 4  # both headers, both columns, bold


def test_position_orientation_header_group_width_matches_axis_line_group_width() -> None:
    from testudo.plugins.builtin.odometry import _GROUP_GAP, _METER_SEGMENTS, _axis_group_header

    plugin = OdometryPlugin()
    plugin.on_message("/odom", _odometry(xx=0.2))
    status = plugin.get_status()
    first_axis_row = status.values["position_orientation"].splitlines()[1]

    # One header group's rendered width (its label/value/cov columns *and*
    # its meter placeholder) plus the group gap must land exactly where the
    # orientation axes' row actually starts ("roll") -- this is the
    # invariant the screenshot-reported bug violated (the header omitted
    # the meter placeholder, so the second header group started 7 columns
    # too early relative to "roll").
    plain_header_group = re.sub(r"\[/?[^\]]+\]", "", _axis_group_header(_METER_SEGMENTS))
    plain_row = re.sub(r"\[/?[^\]]+\]", "", first_axis_row)
    second_axis_start = plain_row.index("roll")
    assert len(plain_header_group) + len(_GROUP_GAP) == second_axis_start


def test_position_orientation_compact_variant_has_no_header_and_is_stacked() -> None:
    plugin = OdometryPlugin()
    plugin.on_message("/odom", _odometry())  # nominal -- no active codes to append
    status = plugin.get_status()
    compact = status.values["position_orientation_compact"]
    assert "value" not in compact
    assert compact.count("\n") == 6  # six stacked one-line axes, no header, trailing blank-line separator


def test_position_orientation_compact_variant_has_fewer_meter_squares() -> None:
    plugin = OdometryPlugin()
    plugin.on_message("/odom", _odometry(xx=0.2))
    status = plugin.get_status()
    wide = status.values["position_orientation"]
    compact = status.values["position_orientation_compact"]

    def square_count(text: str) -> int:
        return text.count("▮") + text.count("▯")

    assert square_count(compact) < square_count(wide)


def test_meter_is_full_and_green_when_nominal() -> None:
    from testudo.plugins.base import ThresholdZone
    from testudo.plugins.builtin.odometry import _METER_SEGMENTS, _meter

    bar = _meter(0.0, ThresholdZone(green=0.01, orange=0.1), _METER_SEGMENTS)
    assert bar == f"[green]{'▮' * _METER_SEGMENTS}[/green]"


def test_meter_is_almost_empty_and_red_when_far_past_orange() -> None:
    from testudo.plugins.base import ThresholdZone
    from testudo.plugins.builtin.odometry import _METER_SEGMENTS, _meter

    bar = _meter(10.0, ThresholdZone(green=0.01, orange=0.1), _METER_SEGMENTS)
    assert bar == f"[bold red]▮[/bold red]{'▯' * (_METER_SEGMENTS - 1)}"


def test_position_orientation_block_reflects_actual_pose_position() -> None:
    plugin = OdometryPlugin()
    msg = _odometry()
    msg.pose.pose.position.x = 1.234
    msg.pose.pose.position.y = -5.678
    plugin.on_message("/odom", msg)
    status = plugin.get_status()
    block = status.values["position_orientation"]
    assert "1.234" in block
    assert "-5.678" in block


def test_quaternion_to_euler_recovers_a_quarter_turn_yaw() -> None:
    from testudo.plugins.builtin.odometry import _quaternion_to_euler

    half_sqrt2 = 0.5**0.5
    roll, pitch, yaw = _quaternion_to_euler(x=0.0, y=0.0, z=half_sqrt2, w=half_sqrt2)  # 90 deg about Z
    assert roll == pytest.approx(0.0, abs=1e-9)
    assert pitch == pytest.approx(0.0, abs=1e-9)
    assert yaw == pytest.approx(math.pi / 2, abs=1e-9)


def test_cmd_vel_check_detail_names_the_cross_checked_topic() -> None:
    plugin = OdometryPlugin(related_topics={"cmd_vel": "/cmd_vel"})
    plugin.on_message("/cmd_vel", _twist(linear_x=0.5))
    plugin.on_message("/odom", _odometry(linear_x=0.0))
    status = plugin.get_status()
    assert "cmd_vel (/cmd_vel):" in status.values["growth_psd"]


def test_nominal_odometry_has_no_error_tags_inline() -> None:
    plugin = OdometryPlugin()
    plugin.on_message("/odom", _odometry())
    status = plugin.get_status()
    assert "ODOM-" not in status.values["position_orientation"]


def test_covariance_trace_over_threshold_shows_odom_001_inline() -> None:
    plugin = OdometryPlugin()
    plugin.on_message("/odom", _odometry(xx=0.2, yy=0.2, zz=0.2))  # trace 0.6 -> ERROR
    status = plugin.get_status()
    block = status.values["position_orientation"]
    assert "ODOM-001" in block
    assert "[bold red]" in block
    assert "ODOM-002" not in block


def test_psd_failure_shows_the_specific_pose_or_twist_code_inline_not_both() -> None:
    plugin = OdometryPlugin()
    msg = _odometry()
    cov = list(msg.pose.covariance)
    cov[0] = -1.0
    msg.pose.covariance = cov
    plugin.on_message("/odom", msg)
    status = plugin.get_status()
    block = status.values["position_orientation"]
    assert "ODOM-004" in block
    assert "ODOM-005" not in block  # twist covariance is untouched (all zero, valid PSD)


def test_multiple_simultaneous_problems_all_show_up_inline() -> None:
    plugin = OdometryPlugin(related_topics={"cmd_vel": "/cmd_vel"})
    plugin.on_message("/cmd_vel", _twist(linear_x=0.5))
    plugin.on_message("/odom", _odometry(xx=0.2, yy=0.2, zz=0.2, linear_x=0.0))  # trace over + cmd_vel mismatch
    status = plugin.get_status()
    block = status.values["position_orientation"]
    assert "ODOM-001" in block
    assert "ODOM-006" in block


def test_check_status_codes_field_stays_empty_since_errors_render_inline() -> None:
    # Avoids the generic Detail Panel fallback double-rendering codes that
    # this plugin already places itself, next to roll/pitch/yaw.
    plugin = OdometryPlugin()
    plugin.on_message("/odom", _odometry(xx=0.2, yy=0.2, zz=0.2))
    status = plugin.get_status()
    assert status.codes == {}
