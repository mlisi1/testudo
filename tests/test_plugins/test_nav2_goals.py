"""Unit tests for Nav2GoalPlugin -- synthetic action_msgs/GoalStatusArray, no live ROS graph."""
from __future__ import annotations

from action_msgs.msg import GoalStatus, GoalStatusArray
from unique_identifier_msgs.msg import UUID

from rich.text import Text

from testudo.plugins.base import Severity, ThresholdZone
from testudo.plugins.builtin.nav2_goals import Nav2GoalPlugin


def _plain(markup: str) -> str:
    return Text.from_markup(markup).plain


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
    assert _plain(status.values["state"]) == "▶ ACCEPTED 0.0s"
    assert _plain(status.values["history"]) == "0✓ 0✗ 0⊘"
    assert "success" not in status.values  # nothing finished yet: left out, not "n/a"


def test_succeeded_goal_counts_toward_success_rate() -> None:
    plugin = Nav2GoalPlugin()
    plugin.on_tick(0.0)
    plugin.on_message("/x", _status_array(_goal_status(1, GoalStatus.STATUS_ACCEPTED)))
    plugin.on_tick(1.0)
    plugin.on_message("/x", _status_array(_goal_status(1, GoalStatus.STATUS_SUCCEEDED)))
    status = plugin.get_status()
    assert status.severity == Severity.OK
    assert _plain(status.values["history"]) == "✓  1✓ 0✗ 0⊘"
    assert _plain(status.values["success"]) == "100% ▮▮▮▮▮▮"
    assert _plain(status.values["state"]) == "◌ idle 0.0s · last ✓ succeeded in 1.0s"
    assert _plain(status.topic_panel_value) == "✓"


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
    assert _plain(status.values["success"]).startswith("50% ")
    assert "success rate" in status.message
    assert "NAV-001" in status.codes
    assert _plain(status.topic_panel_value) == "✓✗"


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
    assert "NAV-002" in status.codes


def test_goal_dropped_from_array_is_no_longer_active() -> None:
    plugin = Nav2GoalPlugin()
    plugin.on_tick(0.0)
    plugin.on_message("/x", _status_array(_goal_status(1, GoalStatus.STATUS_EXECUTING), _goal_status(2, GoalStatus.STATUS_EXECUTING)))
    assert "2 goals" in _plain(plugin.get_status().values["state"])

    plugin.on_tick(1.0)
    plugin.on_message("/x", _status_array())  # server stopped reporting them (goals GC'd)
    assert "goals" not in _plain(plugin.get_status().values["state"])


def test_frequency_threshold_flags_frequent_invocations() -> None:
    plugin = Nav2GoalPlugin(thresholds={"frequency_per_min": ThresholdZone(green=2.0, orange=6.0)})
    for i, t in enumerate([0.0, 5.0, 10.0]):
        plugin.on_tick(t)
        plugin.on_message("/spin/_action/status", _status_array(_goal_status(i, GoalStatus.STATUS_EXECUTING)))
    status = plugin.get_status()
    # 3 acceptances over 10s = 2 intervals/10s * 60 = 12/min -> above orange(6.0)
    assert status.severity == Severity.ERROR
    assert "invocation frequency" in status.message
    assert "NAV-003" in status.codes


def test_default_thresholds_only_cover_success_rate() -> None:
    assert set(Nav2GoalPlugin.default_thresholds()) == {"success_rate"}


def test_current_status_and_time_in_status_track_the_latest_transition() -> None:
    plugin = Nav2GoalPlugin()
    plugin.on_tick(10.0)
    plugin.on_message("/x", _status_array(_goal_status(1, GoalStatus.STATUS_ACCEPTED)))
    status = plugin.get_status()
    assert _plain(status.values["state"]) == "▶ ACCEPTED 0.0s"

    plugin.on_tick(13.0)
    plugin.on_message("/x", _status_array(_goal_status(1, GoalStatus.STATUS_EXECUTING)))
    plugin.on_tick(17.0)
    status = plugin.get_status()
    assert _plain(status.values["state"]) == "▶ EXECUTING 4.0s"


def test_current_status_persists_after_goal_is_garbage_collected() -> None:
    plugin = Nav2GoalPlugin()
    plugin.on_tick(0.0)
    plugin.on_message("/x", _status_array(_goal_status(1, GoalStatus.STATUS_SUCCEEDED)))
    plugin.on_tick(1.0)
    plugin.on_message("/x", _status_array())  # server stopped reporting it
    status = plugin.get_status()
    assert _plain(status.values["state"]).startswith("◌ idle 1.0s · last ✓ succeeded")


def test_no_feedback_yet_reports_remaining_distance_and_time_as_not_available() -> None:
    plugin = Nav2GoalPlugin()
    plugin.on_tick(0.0)
    plugin.on_message("/x", _status_array(_goal_status(1, GoalStatus.STATUS_ACCEPTED)))
    status = plugin.get_status()
    assert _plain(status.values["state"]) == "▶ ACCEPTED 0.0s"  # no "n/a" for distance/ETA


def test_feedback_on_related_topic_populates_remaining_distance_and_time() -> None:
    from builtin_interfaces.msg import Duration
    from nav2_msgs.action._navigate_to_pose import NavigateToPose_Feedback, NavigateToPose_FeedbackMessage

    plugin = Nav2GoalPlugin(related_topics={"feedback": "/navigate_to_pose/_action/feedback"})
    plugin.on_tick(0.0)
    plugin.on_message("/navigate_to_pose/_action/status", _status_array(_goal_status(1, GoalStatus.STATUS_EXECUTING)))

    feedback_msg = NavigateToPose_FeedbackMessage()
    feedback_msg.feedback = NavigateToPose_Feedback()
    feedback_msg.feedback.distance_remaining = 4.5
    feedback_msg.feedback.estimated_time_remaining = Duration(sec=12, nanosec=500_000_000)
    plugin.on_message("/navigate_to_pose/_action/feedback", feedback_msg)

    status = plugin.get_status()
    # Feedback doesn't count as a goal-status update: still one goal executing.
    assert _plain(status.values["state"]) == "▶ EXECUTING 0.0s · 4.5 m left · ETA 12 s"


def test_feedback_without_expected_fields_is_ignored_without_error() -> None:
    from nav2_msgs.action._spin import Spin_Feedback, Spin_FeedbackMessage

    plugin = Nav2GoalPlugin(related_topics={"feedback": "/spin/_action/feedback"})
    plugin.on_tick(0.0)
    plugin.on_message("/spin/_action/status", _status_array(_goal_status(1, GoalStatus.STATUS_EXECUTING)))

    feedback_msg = Spin_FeedbackMessage()
    feedback_msg.feedback = Spin_Feedback()
    plugin.on_message("/spin/_action/feedback", feedback_msg)

    status = plugin.get_status()
    assert _plain(status.values["state"]) == "▶ EXECUTING 0.0s"


def test_canceled_goal_is_neutral_and_durations_sparkline_is_secondary() -> None:
    plugin = Nav2GoalPlugin()
    for goal, (start, end, outcome) in enumerate(
        [(0.0, 2.0, GoalStatus.STATUS_SUCCEEDED), (3.0, 4.0, GoalStatus.STATUS_CANCELED)], start=1
    ):
        plugin.on_tick(start)
        plugin.on_message("/x", _status_array(_goal_status(goal, GoalStatus.STATUS_EXECUTING, accepted_sec=int(start))))
        plugin.on_tick(end)
        plugin.on_message("/x", _status_array(_goal_status(goal, outcome, accepted_sec=int(start))))
    status = plugin.get_status()
    assert status.severity == Severity.OK  # a cancel isn't a failure
    assert _plain(status.values["success"]).startswith("100% ")
    assert _plain(status.values["history"]) == "✓⊘  1✓ 0✗ 1⊘"
    assert _plain(status.values["durations_more"]) == "█▁ (last 2)"
