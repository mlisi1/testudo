"""Unit tests for Nav2GoalPlugin -- synthetic action_msgs/GoalStatusArray, no live ROS graph."""
from __future__ import annotations

from action_msgs.msg import GoalStatus, GoalStatusArray
from unique_identifier_msgs.msg import UUID

from testudo.plugins.base import Severity, ThresholdZone
from testudo.plugins.builtin.nav2_goals import Nav2GoalPlugin


def _goal_id(n: int) -> UUID:
    uuid = UUID()
    uuid.uuid = [n] * 16
    return uuid


def _goal_status(goal_id_n: int, status: int, accepted_sec: int = 0, accepted_nanosec: int = 0) -> GoalStatus:
    gs = GoalStatus()
    gs.goal_info.goal_id = _goal_id(goal_id_n)
    gs.goal_info.stamp.sec = accepted_sec
    gs.goal_info.stamp.nanosec = accepted_nanosec
    gs.status = status
    return gs


def _status_array(*statuses: GoalStatus) -> GoalStatusArray:
    arr = GoalStatusArray()
    arr.status_list = list(statuses)
    return arr


def test_no_messages_yet_is_ok() -> None:
    plugin = Nav2GoalPlugin()
    status = plugin.get_status()
    assert status.severity == Severity.OK
    assert "no goal status received" in status.message


def test_accepted_goal_is_active_and_not_yet_terminal() -> None:
    plugin = Nav2GoalPlugin()
    plugin.on_tick(0.0)
    plugin.on_message("/navigate_to_pose/_action/status", _status_array(_goal_status(1, GoalStatus.STATUS_ACCEPTED)))
    status = plugin.get_status()
    assert status.severity == Severity.OK
    assert status.values["active_count"] == "1"
    assert status.values["succeeded"] == "0"


def test_succeeded_goal_counts_toward_success_rate() -> None:
    plugin = Nav2GoalPlugin()
    plugin.on_tick(0.0)
    plugin.on_message("/x", _status_array(_goal_status(1, GoalStatus.STATUS_ACCEPTED)))
    plugin.on_tick(1.0)
    plugin.on_message("/x", _status_array(_goal_status(1, GoalStatus.STATUS_SUCCEEDED)))
    status = plugin.get_status()
    assert status.severity == Severity.OK
    assert status.values["succeeded"] == "1"
    assert status.values["success_rate"] == "1"
    assert status.values["active_count"] == "0"


def test_low_success_rate_is_flagged() -> None:
    plugin = Nav2GoalPlugin()
    plugin.on_tick(0.0)
    plugin.on_message("/x", _status_array(_goal_status(1, GoalStatus.STATUS_ACCEPTED)))
    plugin.on_message("/x", _status_array(_goal_status(2, GoalStatus.STATUS_ACCEPTED)))
    plugin.on_tick(1.0)
    plugin.on_message(
        "/x",
        _status_array(
            _goal_status(1, GoalStatus.STATUS_SUCCEEDED),
            _goal_status(2, GoalStatus.STATUS_ABORTED),
        ),
    )
    status = plugin.get_status()
    assert status.severity == Severity.ERROR
    assert status.values["success_rate"] == "0.5"
    assert "success rate" in status.message


def test_duration_is_measured_from_acceptance_to_terminal_via_on_tick() -> None:
    plugin = Nav2GoalPlugin()
    plugin.on_tick(10.0)
    plugin.on_message("/x", _status_array(_goal_status(1, GoalStatus.STATUS_ACCEPTED, accepted_sec=10)))
    plugin.on_tick(15.0)
    plugin.on_message("/x", _status_array(_goal_status(1, GoalStatus.STATUS_SUCCEEDED, accepted_sec=10)))
    status = plugin.get_status()
    assert status.values["mean_duration_s"] == "5"


def test_avg_duration_threshold_flags_slow_goals() -> None:
    plugin = Nav2GoalPlugin(thresholds={"avg_duration_s": ThresholdZone(green=5.0, orange=10.0)})
    plugin.on_tick(0.0)
    plugin.on_message("/x", _status_array(_goal_status(1, GoalStatus.STATUS_ACCEPTED)))
    plugin.on_tick(20.0)
    plugin.on_message("/x", _status_array(_goal_status(1, GoalStatus.STATUS_SUCCEEDED)))
    status = plugin.get_status()
    assert status.severity == Severity.ERROR
    assert "mean duration" in status.message


def test_goal_dropped_from_array_is_no_longer_active() -> None:
    plugin = Nav2GoalPlugin()
    plugin.on_tick(0.0)
    plugin.on_message("/x", _status_array(_goal_status(1, GoalStatus.STATUS_EXECUTING)))
    assert plugin.get_status().values["active_count"] == "1"

    plugin.on_tick(1.0)
    plugin.on_message("/x", _status_array())  # server stopped reporting it (goal GC'd)
    assert plugin.get_status().values["active_count"] == "0"


def test_frequency_threshold_flags_frequent_invocations() -> None:
    plugin = Nav2GoalPlugin(thresholds={"frequency_per_min": ThresholdZone(green=2.0, orange=6.0)})
    for i, t in enumerate([0.0, 5.0, 10.0]):
        plugin.on_tick(t)
        plugin.on_message("/spin/_action/status", _status_array(_goal_status(i, GoalStatus.STATUS_EXECUTING)))
    status = plugin.get_status()
    # 3 acceptances over 10s = 2 intervals/10s * 60 = 12/min -> above orange(6.0)
    assert status.severity == Severity.ERROR
    assert "invocation frequency" in status.message


def test_default_thresholds_only_cover_success_rate() -> None:
    assert set(Nav2GoalPlugin.default_thresholds()) == {"success_rate"}
