"""Unit tests for LocalizationPlugin -- synthetic nav_msgs/Odometry (+ Twist), no live ROS graph."""
from __future__ import annotations

import math

import pytest
from geometry_msgs.msg import PoseWithCovarianceStamped, Twist, TwistStamped
from nav_msgs.msg import Odometry

from rich.text import Text

from testudo.plugins.base import Severity, SeverityLevels
from testudo.plugins.builtin.localization import LocalizationPlugin, frame_role


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


def _pose(*, xx: float = 0.0, frame_id: str = "map") -> PoseWithCovarianceStamped:
    msg = PoseWithCovarianceStamped()
    msg.header.frame_id = frame_id
    cov = [0.0] * 36
    cov[0] = xx
    msg.pose.covariance = cov
    msg.pose.pose.orientation.w = 1.0
    return msg


def test_pose_message_is_covered_with_the_same_covariance_checks() -> None:
    assert "geometry_msgs/msg/PoseWithCovarianceStamped" in LocalizationPlugin.msg_types()
    plugin = LocalizationPlugin()
    plugin.on_message("/amcl_pose", _pose(xx=9999.0))
    assert plugin.get_status().code_severities["LOC-001"] == Severity.ERROR


def test_pose_message_reports_no_velocity() -> None:
    plugin = LocalizationPlugin(related_topics={"cmd_vel": "/cmd_vel"})
    plugin.on_message("/cmd_vel", _twist(linear_x=0.5))
    plugin.on_message("/amcl_pose", _pose())
    status = plugin.get_status()
    assert "LOC-006" not in status.codes  # a pose can't say the robot isn't moving
    assert "linear_x_m_per_s" not in status.metrics
    assert "twist_σ_more" not in status.values


def test_stamped_cmd_vel_is_read_like_a_plain_one() -> None:
    plugin = LocalizationPlugin(related_topics={"cmd_vel": "/cmd_vel"})
    stamped = TwistStamped()
    stamped.twist.linear.x = 0.5
    plugin.on_message("/cmd_vel", stamped)
    plugin.on_message("/odom", _odometry(linear_x=0.0))
    assert "LOC-006" in plugin.get_status().codes


def _framed_odometry(frame_id: str, **kwargs) -> Odometry:
    msg = _odometry(**kwargs)
    msg.header.frame_id = frame_id
    return msg


@pytest.mark.parametrize(
    "frame_id, role",
    [("odom", "continuous"), ("robot1/odom", "continuous"), ("map", "global"), ("earth", "global"), ("base_link", None), ("", None)],
)
def test_frame_role_comes_from_the_last_segment_of_the_frame(frame_id: str, role: str | None) -> None:
    assert frame_role(frame_id) == role


def test_continuous_frame_tolerates_covariance_growth_but_not_a_placeholder() -> None:
    plugin = LocalizationPlugin()
    plugin.on_message("/odom", _framed_odometry("odom", stamp_sec=0, xx=0.0))
    plugin.on_message("/odom", _framed_odometry("odom", stamp_sec=1, xx=5.0))
    assert plugin.get_status().codes == {}
    plugin.on_message("/odom", _framed_odometry("odom", stamp_sec=2, xx=9999.0))
    assert plugin.get_status().code_severities["LOC-001"] == Severity.ERROR


def test_global_frame_flags_growing_covariance() -> None:
    plugin = LocalizationPlugin()
    plugin.on_message("/pose", _framed_odometry("map", stamp_sec=0, xx=0.0))
    plugin.on_message("/pose", _framed_odometry("map", stamp_sec=1, xx=0.2))
    assert set(plugin.get_status().codes) == {"LOC-003"}


def test_global_frame_accepts_a_particle_filters_usual_covariance() -> None:
    plugin = LocalizationPlugin()
    plugin.on_message("/amcl_pose", _pose(xx=0.25))
    assert plugin.get_status().codes == {}


def test_an_unknown_frame_keeps_the_strict_defaults() -> None:
    plugin = LocalizationPlugin()
    plugin.on_message("/pose", _framed_odometry("base_link", xx=0.2))
    assert "LOC-001" in plugin.get_status().codes


def test_levels_written_in_config_win_over_the_role_defaults() -> None:
    plugin = LocalizationPlugin()
    plugin.levels = {**plugin.levels, "position_covariance_trace": SeverityLevels(warn=0.05, error=1000.0)}
    plugin.on_message("/odom", _framed_odometry("odom", xx=0.2))
    assert "LOC-001" in plugin.get_status().codes


def test_configured_role_overrides_the_frame_and_survives_a_reset() -> None:
    plugin = LocalizationPlugin()
    plugin.set_frame_role("global")
    plugin.reset()
    plugin.on_message("/odom", _framed_odometry("odom", stamp_sec=0, xx=0.0))
    plugin.on_message("/odom", _framed_odometry("odom", stamp_sec=1, xx=0.2))
    assert plugin.get_status().metrics["frame_role"] == "global"
    assert "LOC-003" in plugin.get_status().codes


def test_global_frame_skips_the_cmd_vel_cross_check() -> None:
    plugin = LocalizationPlugin(related_topics={"cmd_vel": "/cmd_vel"})
    plugin.on_message("/cmd_vel", _twist(linear_x=0.5))
    plugin.on_message("/pose", _framed_odometry("map", linear_x=0.0))
    assert "LOC-006" not in plugin.get_status().codes


def _at(frame_id: str, stamp_sec: int, x: float = 0.0, yaw: float = 0.0, linear_x: float = 0.0) -> Odometry:
    msg = _framed_odometry(frame_id, stamp_sec=stamp_sec, linear_x=linear_x)
    msg.pose.pose.position.x = x
    msg.pose.pose.orientation.z = math.sin(yaw / 2.0)
    msg.pose.pose.orientation.w = math.cos(yaw / 2.0)
    return msg


def _feed(plugin: LocalizationPlugin, *messages: Odometry) -> None:
    for msg in messages:
        plugin.on_tick(float(msg.header.stamp.sec) + 100.0)
        plugin.on_message("/topic", msg)


def test_motion_the_twist_explains_is_not_a_jump() -> None:
    plugin = LocalizationPlugin()
    _feed(plugin, _at("odom", 1, x=0.0, linear_x=1.0), _at("odom", 2, x=1.0, linear_x=1.0))
    assert "LOC-007" not in plugin.get_status().codes


def test_a_continuous_topic_flags_a_small_jump() -> None:
    plugin = LocalizationPlugin()
    _feed(plugin, _at("odom", 1, x=0.0), _at("odom", 2, x=0.3))
    assert plugin.get_status().code_severities["LOC-007"] == Severity.WARN


def test_a_large_continuous_jump_is_an_error() -> None:
    plugin = LocalizationPlugin()
    _feed(plugin, _at("odom", 1, x=0.0), _at("odom", 2, x=3.0))
    assert plugin.get_status().code_severities["LOC-007"] == Severity.ERROR


def test_a_heading_jump_counts_and_wraps_around_pi() -> None:
    plugin = LocalizationPlugin()
    _feed(plugin, _at("odom", 1, yaw=3.1), _at("odom", 2, yaw=-3.1))
    assert "LOC-007" not in plugin.get_status().codes  # 0.08 rad apart, not 6.2
    _feed(plugin, _at("odom", 3, yaw=1.0))
    assert "LOC-007" in plugin.get_status().codes


def test_a_jump_stays_active_for_a_while_then_clears() -> None:
    plugin = LocalizationPlugin()
    _feed(plugin, _at("odom", 1, x=0.0), _at("odom", 2, x=0.3))
    plugin.on_tick(102.0 + 9.0)
    assert "LOC-007" in plugin.get_status().codes
    plugin.on_tick(102.0 + 10.0)
    status = plugin.get_status()
    assert "LOC-007" not in status.codes
    assert status.metrics["pose_jumps"] == 1


def test_a_global_topic_tolerates_a_relocalization_but_flags_a_large_one() -> None:
    plugin = LocalizationPlugin()
    _feed(plugin, _at("map", 1, x=0.0), _at("map", 2, x=1.0))
    assert "LOC-007" not in plugin.get_status().codes
    _feed(plugin, _at("map", 3, x=6.0))
    assert plugin.get_status().code_severities["LOC-007"] == Severity.WARN


def test_a_burst_of_relocalizations_is_flagged() -> None:
    plugin = LocalizationPlugin()
    _feed(plugin, _at("map", 1, x=0.0), _at("map", 2, x=1.0), _at("map", 3, x=0.0), _at("map", 4, x=1.0))
    assert "LOC-008" not in plugin.get_status().codes  # three in a minute: on the limit, not past it
    _feed(plugin, _at("map", 5, x=0.0))
    assert plugin.get_status().code_severities["LOC-008"] == Severity.WARN


def test_relocalizations_age_out_of_the_burst_window() -> None:
    plugin = LocalizationPlugin()
    _feed(plugin, *(_at("map", second, x=float(second % 2)) for second in range(1, 6)))
    assert "LOC-008" in plugin.get_status().codes
    plugin.on_tick(105.0 + 60.0)
    assert "LOC-008" not in plugin.get_status().codes


def test_a_new_frame_is_not_compared_with_the_old_one() -> None:
    plugin = LocalizationPlugin()
    _feed(plugin, _at("odom", 1, x=0.0), _at("odom_alt", 2, x=50.0))
    assert "LOC-007" not in plugin.get_status().codes


def test_a_pose_without_velocity_is_credited_a_plausible_speed() -> None:
    plugin = LocalizationPlugin()
    plugin.set_frame_role("continuous")
    first, second = _pose(), _pose()
    first.header.stamp.sec, second.header.stamp.sec = 1, 2
    second.pose.pose.position.x = 2.0  # 2 m/s
    _feed(plugin, first, second)
    assert "LOC-007" not in plugin.get_status().codes


POSE_TYPE = "geometry_msgs/msg/PoseWithCovarianceStamped"


def test_a_pose_estimate_may_be_silent_before_its_first_message_and_at_rest() -> None:
    plugin = LocalizationPlugin()
    assert plugin.silence_expected(POSE_TYPE)
    plugin.on_tick(100.0)
    plugin.on_message("/amcl_pose", _pose())
    plugin.on_tick(500.0)
    assert plugin.silence_expected(POSE_TYPE)


def test_odometry_is_never_expected_to_go_silent() -> None:
    plugin = LocalizationPlugin()
    plugin.on_message("/pose", _framed_odometry("map"))
    assert not plugin.silence_expected("nav_msgs/msg/Odometry")


def test_a_pose_in_a_continuous_frame_is_not_expected_to_go_silent() -> None:
    plugin = LocalizationPlugin()
    plugin.on_message("/pose", _pose(frame_id="odom"))
    assert not plugin.silence_expected(POSE_TYPE)


def _commanded_pose_plugin(start: float = 100.0) -> LocalizationPlugin:
    plugin = LocalizationPlugin(related_topics={"cmd_vel": "/cmd_vel"})
    plugin.on_tick(start)
    plugin.on_message("/amcl_pose", _pose())
    return plugin


def _command(plugin: LocalizationPlugin, now: float, linear_x: float = 0.5) -> None:
    plugin.on_tick(now)
    plugin.on_message("/cmd_vel", _twist(linear_x=linear_x))


def test_a_pose_silent_while_the_robot_is_told_to_move_is_a_fault() -> None:
    plugin = _commanded_pose_plugin()
    for second in range(101, 110):
        _command(plugin, float(second))
    assert plugin.silence_expected(POSE_TYPE)  # still within the grace period
    _command(plugin, 111.0)
    assert not plugin.silence_expected(POSE_TYPE)


def test_a_new_pose_while_moving_restarts_the_grace_period() -> None:
    plugin = _commanded_pose_plugin()
    for second in range(101, 112):
        _command(plugin, float(second))
    plugin.on_message("/amcl_pose", _pose())
    assert plugin.silence_expected(POSE_TYPE)


def test_stopping_makes_silence_normal_again() -> None:
    plugin = _commanded_pose_plugin()
    for second in range(101, 112):
        _command(plugin, float(second))
    _command(plugin, 112.0, linear_x=0.0)
    assert plugin.silence_expected(POSE_TYPE)


def test_a_cmd_vel_that_stopped_arriving_says_nothing() -> None:
    plugin = _commanded_pose_plugin()
    for second in range(101, 112):
        _command(plugin, float(second))
    plugin.on_tick(120.0)
    assert plugin.silence_expected(POSE_TYPE)


def _twist(linear_x: float = 0.0, angular_z: float = 0.0) -> Twist:
    msg = Twist()
    msg.linear.x = linear_x
    msg.angular.z = angular_z
    return msg


def test_no_messages_yet_is_ok() -> None:
    plugin = LocalizationPlugin()
    status = plugin.get_status()
    assert status.severity == Severity.OK
    assert "no localization data received" in status.message


def test_nominal_zero_covariance_is_ok() -> None:
    plugin = LocalizationPlugin()
    plugin.on_message("/odom", _odometry())
    status = plugin.get_status()
    assert status.severity == Severity.OK


def test_high_but_real_position_covariance_is_a_warning() -> None:
    plugin = LocalizationPlugin()
    plugin.on_message("/odom", _odometry(xx=0.2, yy=0.2, zz=0.2))  # trace 0.6: degraded, not broken
    status = plugin.get_status()
    assert status.severity == Severity.WARN
    assert "position covariance trace" in status.message


def test_placeholder_position_covariance_is_an_error() -> None:
    plugin = LocalizationPlugin()
    plugin.on_message("/odom", _odometry(xx=9999.0, yy=9999.0, zz=9999.0))  # "unknown" placeholders
    status = plugin.get_status()
    assert status.severity == Severity.ERROR
    assert status.code_severities["LOC-001"] == Severity.ERROR


def test_yaw_variance_is_a_warning_until_the_heading_is_unknown() -> None:
    plugin = LocalizationPlugin()
    plugin.on_message("/odom", _odometry(yaw_variance=0.5))
    status = plugin.get_status()
    assert status.severity == Severity.WARN
    assert "yaw variance" in status.message
    plugin.on_message("/odom", _odometry(yaw_variance=20.0))  # σ > π
    assert plugin.get_status().severity == Severity.ERROR


def test_negative_diagonal_covariance_fails_psd_check() -> None:
    plugin = LocalizationPlugin()
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
    assert set(status.codes) == {"LOC-004"}  # twist covariance is untouched (all zero, valid PSD)


def test_rapid_covariance_growth_is_flagged() -> None:
    plugin = LocalizationPlugin()
    plugin.on_message("/odom", _odometry(stamp_sec=0, xx=0.0))
    plugin.on_message("/odom", _odometry(stamp_sec=0, stamp_nanosec=10_000_000, xx=0.05))  # +0.05 in 10ms = 5/s
    status = plugin.get_status()
    # Growth never breaks a topic on its own: a diverging filter trips the trace.
    assert status.severity == Severity.WARN
    assert "covariance growing" in status.message


def test_cmd_vel_cross_check_without_related_topic_configured_is_ok() -> None:
    plugin = LocalizationPlugin()
    plugin.on_message("/odom", _odometry(linear_x=0.0))
    status = plugin.get_status()
    assert "cmd     – m/s" in _plain(status.values["axes"])
    assert status.severity == Severity.OK


def test_cmd_vel_cross_check_flags_commanded_motion_with_no_odometry_movement() -> None:
    plugin = LocalizationPlugin(related_topics={"cmd_vel": "/cmd_vel"})
    plugin.on_message("/cmd_vel", _twist(linear_x=0.5))
    plugin.on_message("/odom", _odometry(linear_x=0.0, angular_z=0.0))
    status = plugin.get_status()
    assert status.severity == Severity.WARN
    assert "cmd_vel commands motion" in status.message
    assert "0.00  cmd  0.50 m/s" in _plain(status.values["axes"])
    assert "LOC-006" in status.codes


def test_cmd_vel_cross_check_passes_when_odometry_matches_commanded_motion() -> None:
    plugin = LocalizationPlugin(related_topics={"cmd_vel": "/cmd_vel"})
    plugin.on_message("/cmd_vel", _twist(linear_x=0.5))
    plugin.on_message("/odom", _odometry(linear_x=0.5))
    status = plugin.get_status()
    assert status.severity == Severity.OK


def test_cmd_vel_message_routes_by_topic_name_not_message_type() -> None:
    plugin = LocalizationPlugin(related_topics={"cmd_vel": "/cmd_vel"})
    plugin.on_message("/cmd_vel", _twist(linear_x=1.0))
    # No odometry message yet -- only the cmd_vel side has been seen.
    assert plugin.get_status().message == "no localization data received yet"


def test_default_thresholds_cover_all_localization_metrics() -> None:
    thresholds = LocalizationPlugin.default_thresholds()
    assert set(thresholds) == {"position_covariance_trace", "yaw_variance", "covariance_growth_rate"}


def test_no_messages_yet_has_no_topic_panel_column() -> None:
    # Unreachable via the live TUI (liveness always pre-empts this status
    # first -- see TUI_DATA_DESIGN.md), but still correct in isolation.
    plugin = LocalizationPlugin()
    status = plugin.get_status()
    assert status.topic_panel_column == ""
    assert status.topic_panel_value == ""


def test_topic_panel_column_is_horizontal_sigma() -> None:
    plugin = LocalizationPlugin()
    plugin.on_message("/odom", _odometry(xx=0.03, yy=0.0076))
    status = plugin.get_status()
    assert status.topic_panel_column == "σxy"
    assert _plain(status.topic_panel_value) == "±0.19"


def test_topic_panel_value_is_colored_by_the_trace_severity_with_a_growth_arrow() -> None:
    plugin = LocalizationPlugin()
    plugin.on_message("/odom", _odometry(stamp_sec=0, xx=0.0))
    plugin.on_message("/odom", _odometry(stamp_sec=1, xx=0.2, yy=0.2, zz=0.2))  # trace 0.6 past warn, growing
    status = plugin.get_status()
    assert "[yellow]" in status.topic_panel_value
    assert _plain(status.topic_panel_value).endswith("↑")


def test_growth_rate_is_shown_with_a_meter_colored_by_its_zone() -> None:
    plugin = LocalizationPlugin()
    plugin.on_message("/odom", _odometry(stamp_sec=0, xx=0.0))
    plugin.on_message("/odom", _odometry(stamp_sec=0, stamp_nanosec=10_000_000, xx=0.05))  # +0.05 in 10ms = 5/s -> ERROR
    status = plugin.get_status()
    assert "growth +5.000 m²/s" in _plain(status.values["σxy"])
    assert "[bold red]▮" in status.values["σxy"]
    assert "LOC-003" in status.codes


def test_axes_block_has_a_row_per_axis_with_sigma_and_motion_side_by_side() -> None:
    plugin = LocalizationPlugin()
    plugin.on_message("/odom", _odometry(xx=0.0676, yaw_variance=0.0001))
    rows = _plain(plugin.get_status().values["axes"]).splitlines()
    assert len(rows) == 3
    assert rows[0].startswith("x      ±0.26m ")
    assert "roll" in rows[0] and rows[0].rstrip().endswith("m/s")
    assert "yaw    ±0.57°" in rows[2] and "PSD" in rows[2]
    # The orientation group starts in the same column on every row.
    assert len({row.index(axis) for row, axis in zip(rows, ("roll", "pitch", "yaw"))}) == 1


def test_axes_compact_variant_stacks_motion_under_one_square_meters() -> None:
    plugin = LocalizationPlugin()
    plugin.on_message("/odom", _odometry(xx=0.2))
    status = plugin.get_status()
    compact = _plain(status.values["axes_compact"]).splitlines()
    assert len(compact) == 6
    assert compact[0].count("▮") + compact[0].count("▯") == 2  # one square each for x and roll


def test_sigma_xy_trend_samples_once_a_second_on_tick() -> None:
    plugin = LocalizationPlugin()
    plugin.on_tick(1.0)
    plugin.on_message("/odom", _odometry(xx=0.01))
    for t in (1.0, 1.5, 2.0, 3.0):
        plugin.on_tick(t)
    plugin.on_message("/odom", _odometry(xx=0.04))
    plugin.on_tick(4.0)
    value = _plain(plugin.get_status().values["σxy"])
    assert value.startswith("▅▅▅█ (4 s)")  # σ 0.1 then 0.2, scaled from 0


def test_frame_pair_and_secondary_values() -> None:
    plugin = LocalizationPlugin()
    msg = _odometry(stamp_sec=9)
    msg.header.frame_id = "odom"
    msg.child_frame_id = "base_link"
    msg.pose.pose.position.x = 1.234
    plugin.on_tick(9.012)
    plugin.on_message("/odom", msg)
    values = plugin.get_status().values
    assert _plain(values["σxy"]).endswith("odom → base_link · continuous")
    assert "x 1.234" in _plain(values["pose_more"])
    assert "twist_σ_more" in values


def test_codes_go_in_the_generic_code_lines() -> None:
    plugin = LocalizationPlugin(related_topics={"cmd_vel": "/cmd_vel"})
    plugin.on_message("/cmd_vel", _twist(linear_x=0.5))
    plugin.on_message("/odom", _odometry(xx=0.2, yy=0.2, zz=0.2, linear_x=0.0))  # trace over + cmd_vel mismatch
    status = plugin.get_status()
    assert set(status.codes) == {"LOC-001", "LOC-006"}
    assert "[yellow]" in status.codes["LOC-001"]


def test_quaternion_to_euler_recovers_a_quarter_turn_yaw() -> None:
    from testudo.plugins.builtin.localization import _quaternion_to_euler

    half_sqrt2 = 0.5**0.5
    roll, pitch, yaw = _quaternion_to_euler(x=0.0, y=0.0, z=half_sqrt2, w=half_sqrt2)  # 90 deg about Z
    assert roll == pytest.approx(0.0, abs=1e-9)
    assert pitch == pytest.approx(0.0, abs=1e-9)
    assert yaw == pytest.approx(math.pi / 2, abs=1e-9)


def test_split_zone_divides_every_configured_boundary() -> None:
    from testudo.plugins.base import ThresholdZone
    from testudo.plugins.builtin.localization import _split_zone

    zone = _split_zone(ThresholdZone(green=0.03, orange=0.3), 3)
    assert zone.green == pytest.approx(0.01)
    assert zone.orange == pytest.approx(0.1)
    assert zone.red is None
    assert _split_zone(None, 3) is None


def test_metrics_publish_raw_variances_speeds_and_frames() -> None:
    plugin = LocalizationPlugin()
    plugin.on_message("/odom", _odometry(xx=0.04, yy=0.09, linear_x=0.5))
    metrics = plugin.get_status().metrics
    assert metrics["position_variance_x_m2"] == pytest.approx(0.04)
    assert metrics["sigma_xy_m"] == pytest.approx(math.sqrt(0.13))
    assert metrics["linear_x_m_per_s"] == 0.5
    assert metrics["pose_psd_ok"] is True
    assert "cmd_linear_x_m_per_s" not in metrics  # no cmd_vel seen
