"""Unit tests for BehaviorTreeLogPlugin -- synthetic nav2_msgs/BehaviorTreeLog, no live ROS graph."""
from __future__ import annotations

from nav2_msgs.msg import BehaviorTreeLog, BehaviorTreeStatusChange

from testudo.plugins.base import Severity, ThresholdZone
from testudo.plugins.builtin.bt_log import BehaviorTreeLogPlugin


def _change(node_name: str, previous_status: str, current_status: str) -> BehaviorTreeStatusChange:
    c = BehaviorTreeStatusChange()
    c.node_name = node_name
    c.previous_status = previous_status
    c.current_status = current_status
    return c


def _log(*changes: BehaviorTreeStatusChange) -> BehaviorTreeLog:
    msg = BehaviorTreeLog()
    msg.event_log = list(changes)
    return msg


def test_no_events_yet_is_ok() -> None:
    plugin = BehaviorTreeLogPlugin()
    status = plugin.get_status()
    assert status.severity == Severity.OK
    assert "no behavior tree log received" in status.message


def test_node_entering_running_becomes_active() -> None:
    plugin = BehaviorTreeLogPlugin()
    plugin.on_tick(0.0)
    plugin.on_message("/behavior_tree_log", _log(_change("NavigateRecovery", "IDLE", "RUNNING")))
    status = plugin.get_status()
    assert status.values["active_nodes"] == "NavigateRecovery"
    assert status.values["active_node_count"] == "1"


def test_node_leaving_running_is_no_longer_active() -> None:
    plugin = BehaviorTreeLogPlugin()
    plugin.on_tick(0.0)
    plugin.on_message("/behavior_tree_log", _log(_change("NavigateRecovery", "IDLE", "RUNNING")))
    plugin.on_tick(1.0)
    plugin.on_message("/behavior_tree_log", _log(_change("NavigateRecovery", "RUNNING", "SUCCESS")))
    status = plugin.get_status()
    assert status.values["active_nodes"] == "none"
    assert status.values["active_node_count"] == "0"


def test_multiple_ancestors_are_simultaneously_active() -> None:
    plugin = BehaviorTreeLogPlugin()
    plugin.on_tick(0.0)
    plugin.on_message(
        "/behavior_tree_log",
        _log(
            _change("RecoveryFallback", "IDLE", "RUNNING"),
            _change("Spin", "IDLE", "RUNNING"),
        ),
    )
    status = plugin.get_status()
    assert status.values["active_node_count"] == "2"
    assert "Spin" in status.values["active_nodes"]
    assert "RecoveryFallback" in status.values["active_nodes"]


def test_recovery_node_trigger_is_counted() -> None:
    plugin = BehaviorTreeLogPlugin()
    plugin.on_tick(0.0)
    plugin.on_message("/behavior_tree_log", _log(_change("Spin", "IDLE", "RUNNING")))
    status = plugin.get_status()
    assert status.values["recovery_trigger_count"] == "1"
    assert status.values["last_recovery_node"] == "Spin"


def test_non_recovery_node_trigger_is_not_counted_as_recovery() -> None:
    plugin = BehaviorTreeLogPlugin()
    plugin.on_tick(0.0)
    plugin.on_message("/behavior_tree_log", _log(_change("ComputePathToPose", "IDLE", "RUNNING")))
    status = plugin.get_status()
    assert status.values["recovery_trigger_count"] == "0"
    assert status.values["last_recovery_node"] == "n/a"


def test_recovery_node_name_with_bt_cpp_instance_suffix_is_still_recognized() -> None:
    plugin = BehaviorTreeLogPlugin()
    plugin.on_tick(0.0)
    plugin.on_message("/behavior_tree_log", _log(_change("ClearEntireCostmap-Context", "IDLE", "RUNNING")))
    assert plugin.get_status().values["recovery_trigger_count"] == "1"


def test_repeated_running_without_leaving_is_not_a_new_trigger() -> None:
    plugin = BehaviorTreeLogPlugin()
    plugin.on_tick(0.0)
    plugin.on_message("/behavior_tree_log", _log(_change("Spin", "IDLE", "RUNNING")))
    plugin.on_tick(1.0)
    # Same node re-reported RUNNING without an intervening non-RUNNING status.
    plugin.on_message("/behavior_tree_log", _log(_change("Spin", "RUNNING", "RUNNING")))
    assert plugin.get_status().values["recovery_trigger_count"] == "1"


def test_recovery_frequency_threshold_flags_frequent_triggers() -> None:
    plugin = BehaviorTreeLogPlugin(thresholds={"recovery_frequency_per_min": ThresholdZone(green=2.0, orange=6.0)})
    for t in (0.0, 5.0, 10.0):
        plugin.on_tick(t)
        plugin.on_message("/behavior_tree_log", _log(_change("Spin", "SUCCESS", "RUNNING")))
        plugin.on_tick(t + 0.1)
        plugin.on_message("/behavior_tree_log", _log(_change("Spin", "RUNNING", "SUCCESS")))
    status = plugin.get_status()
    # 3 triggers over 10s = 2 intervals/10s * 60 = 12/min -> above orange(6.0)
    assert status.severity == Severity.ERROR
    assert "recovery frequency" in status.message


def test_default_thresholds_are_empty() -> None:
    assert BehaviorTreeLogPlugin.default_thresholds() == {}
