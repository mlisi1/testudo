"""Unit tests for VelocityCommandPlugin -- synthetic messages, no live ROS graph."""
from __future__ import annotations

from action_msgs.msg import GoalStatus, GoalStatusArray
from geometry_msgs.msg import Twist, TwistStamped
from nav_msgs.msg import Odometry

from testudo.plugins.base import Severity
from testudo.plugins.builtin.velocity_command import VelocityCommandPlugin

_RELATED = {"odom": "/odom"}


def _twist(linear: float = 0.0, angular: float = 0.0) -> Twist:
    msg = Twist()
    msg.linear.x, msg.angular.z = linear, angular
    return msg


def _odom(linear: float = 0.0, angular: float = 0.0) -> Odometry:
    msg = Odometry()
    msg.twist.twist.linear.x, msg.twist.twist.angular.z = linear, angular
    return msg


def _goal(status: int) -> GoalStatusArray:
    return GoalStatusArray(status_list=[GoalStatus(status=status)])


def _drive(plugin: VelocityCommandPlugin, seconds: float, command: Twist, odom: Odometry | None = None, start: float = 100.0) -> float:
    """Feed commands (and odometry) at 10 Hz; the time reached."""
    steps = int(seconds * 10)
    for step in range(steps + 1):
        now = start + step * 0.1
        plugin.on_tick(now)
        if odom is not None:
            plugin.on_message("/odom", odom)
        plugin.on_message("/cmd_vel", command)
    return start + steps * 0.1


def test_covers_both_wire_formats_and_every_message() -> None:
    assert VelocityCommandPlugin.msg_types() == ("geometry_msgs/msg/Twist", "geometry_msgs/msg/TwistStamped")
    assert VelocityCommandPlugin.needs_every_message()


def test_silence_is_allowed() -> None:
    assert VelocityCommandPlugin().silence_expected("geometry_msgs/msg/Twist")


def test_no_commands_yet_is_ok() -> None:
    assert "no commands" in VelocityCommandPlugin().get_status().message


def test_a_stamped_command_is_read() -> None:
    plugin = VelocityCommandPlugin()
    msg = TwistStamped()
    msg.twist.linear.x = 0.4
    plugin.on_message("/cmd_vel", msg)
    assert plugin.get_status().metrics["linear_m_per_s"] == 0.4


def test_a_nan_command_is_an_error_held_for_a_while() -> None:
    plugin = VelocityCommandPlugin()
    plugin.on_tick(100.0)
    plugin.on_message("/cmd_vel", _twist(float("nan")))
    assert plugin.get_status().code_severities == {"VEL-001": Severity.ERROR}
    plugin.on_tick(111.0)
    assert plugin.get_status().codes == {}


def test_a_robot_following_its_command_is_fine() -> None:
    plugin = VelocityCommandPlugin(related_topics=_RELATED)
    _drive(plugin, 12.0, _twist(0.5), _odom(0.48))
    assert plugin.get_status().codes == {}


def test_a_robot_not_moving_warns_then_errors() -> None:
    plugin = VelocityCommandPlugin(related_topics=_RELATED)
    now = _drive(plugin, 4.0, _twist(0.5), _odom())
    assert plugin.get_status().code_severities == {"VEL-002": Severity.WARN}
    _drive(plugin, 7.0, _twist(0.5), _odom(), start=now)
    assert plugin.get_status().code_severities == {"VEL-002": Severity.ERROR}


def test_a_robot_crawling_far_below_its_command_is_stuck() -> None:
    plugin = VelocityCommandPlugin(related_topics=_RELATED)
    _drive(plugin, 4.0, _twist(1.0), _odom(0.05))
    assert "VEL-002" in plugin.get_status().codes


def test_turning_in_place_counts_as_moving() -> None:
    plugin = VelocityCommandPlugin(related_topics=_RELATED)
    _drive(plugin, 4.0, _twist(0.0, 0.8 ), _odom(0.0, 0.75))
    assert "VEL-002" not in plugin.get_status().codes


def test_stuck_clears_once_the_robot_moves() -> None:
    plugin = VelocityCommandPlugin(related_topics=_RELATED)
    now = _drive(plugin, 4.0, _twist(0.5), _odom())
    _drive(plugin, 1.0, _twist(0.5), _odom(0.5), start=now)
    assert plugin.get_status().codes == {}


def test_stuck_clears_when_the_commands_stop() -> None:
    plugin = VelocityCommandPlugin(related_topics=_RELATED)
    now = _drive(plugin, 4.0, _twist(0.5), _odom())
    plugin.on_tick(now + 2.0)
    assert plugin.get_status().codes == {}


def test_without_odometry_nothing_is_called_stuck() -> None:
    plugin = VelocityCommandPlugin()
    _drive(plugin, 12.0, _twist(0.5))
    assert plugin.get_status().codes == {}


def test_odometry_gone_silent_is_not_called_stuck() -> None:
    plugin = VelocityCommandPlugin(related_topics=_RELATED)
    plugin.on_tick(100.0)
    plugin.on_message("/odom", _odom())
    _drive(plugin, 12.0, _twist(0.5), start=102.0)
    assert plugin.get_status().codes == {}


def test_stuck_is_only_judged_while_a_goal_executes_when_goals_are_watched() -> None:
    plugin = VelocityCommandPlugin(related_topics={**_RELATED, "goal_status": "/nav/_action/status"})
    now = _drive(plugin, 4.0, _twist(0.5), _odom())
    assert plugin.get_status().codes == {}
    plugin.on_message("/nav/_action/status", _goal(GoalStatus.STATUS_EXECUTING))
    now = _drive(plugin, 4.0, _twist(0.5), _odom(), start=now)
    assert "VEL-002" in plugin.get_status().codes
    plugin.on_message("/nav/_action/status", _goal(GoalStatus.STATUS_SUCCEEDED))
    plugin.on_tick(now + 0.1)
    assert plugin.get_status().codes == {}


def _oscillate(plugin: VelocityCommandPlugin, period_steps: int, seconds: float = 12.0) -> None:
    for step in range(int(seconds * 20)):
        plugin.on_tick(100.0 + step * 0.05)
        sign = 1.0 if (step // period_steps) % 2 == 0 else -1.0
        plugin.on_message("/cmd_vel", _twist(0.2, sign * 0.5))


def test_a_command_flipping_every_message_is_oscillating() -> None:
    plugin = VelocityCommandPlugin()
    _oscillate(plugin, period_steps=1)
    status = plugin.get_status()
    assert status.code_severities == {"VEL-003": Severity.WARN}
    assert status.metrics["reversals_per_s"] > 1.0


def test_occasional_steering_corrections_are_not_oscillation() -> None:
    plugin = VelocityCommandPlugin()
    _oscillate(plugin, period_steps=40)
    assert plugin.get_status().codes == {}


def test_noise_around_zero_is_not_oscillation() -> None:
    plugin = VelocityCommandPlugin()
    for step in range(240):
        plugin.on_tick(100.0 + step * 0.05)
        plugin.on_message("/cmd_vel", _twist(0.3, 0.01 * (-1) ** step))
    assert plugin.get_status().codes == {}


def test_a_pinned_turn_rate_is_flagged() -> None:
    plugin = VelocityCommandPlugin()
    _drive(plugin, 6.0, _twist(0.2, 1.0))
    status = plugin.get_status()
    assert status.code_severities == {"VEL-004": Severity.WARN}
    assert "pinned at 1.00" in status.codes["VEL-004"]


def test_cruising_at_a_constant_speed_is_fine() -> None:
    plugin = VelocityCommandPlugin()
    _drive(plugin, 12.0, _twist(0.5))
    assert plugin.get_status().codes == {}


def test_a_varying_turn_rate_is_not_pinned() -> None:
    plugin = VelocityCommandPlugin()
    for step in range(100):
        plugin.on_tick(100.0 + step * 0.1)
        plugin.on_message("/cmd_vel", _twist(0.2, 0.5 + 0.001 * step))
    assert plugin.get_status().codes == {}


def test_teleop_holding_a_turn_is_left_alone_when_goals_are_watched() -> None:
    plugin = VelocityCommandPlugin(related_topics={"goal_status": "/nav/_action/status"})
    _drive(plugin, 6.0, _twist(0.0, 1.0))
    assert plugin.get_status().codes == {}


def test_a_problem_reads_as_plain_text_and_reversing_shows_its_sign() -> None:
    plugin = VelocityCommandPlugin(related_topics=_RELATED)
    _drive(plugin, 4.0, _twist(-0.5), _odom())
    status = plugin.get_status()
    assert status.message.startswith("commanded to move for")
    assert status.metrics["linear_m_per_s"] == -0.5


def test_a_topic_gone_quiet_does_not_read_as_still_commanding() -> None:
    plugin = VelocityCommandPlugin()
    now = _drive(plugin, 1.0, _twist(0.2, 0.1))
    plugin.on_tick(now + 5.0)
    assert plugin.get_status().message == "silent, last command 0.20 m/s, +0.10 rad/s"


def test_the_panel_shows_the_command() -> None:
    plugin = VelocityCommandPlugin(related_topics=_RELATED)
    _drive(plugin, 0.5, _twist(0.5, -0.25), _odom(0.5, -0.25))
    status = plugin.get_status()
    assert status.topic_panel_column == "Cmd"
    assert status.topic_panel_value == "0.50 -0.25"
    assert status.message == "commanding 0.50 m/s, -0.25 rad/s"
    assert status.metrics["stuck_s"] == 0.0
