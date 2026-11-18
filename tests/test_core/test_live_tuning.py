"""Unit tests for changing a running session's checks: no live graph, a stand-in for the manager's state."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from testudo.core import live_tuning
from testudo.core.config import ActionConfig, LevelsOverride, TopicConfig
from testudo.core.stamp_check import STAMP_LAG_METRIC, StampCheck
from testudo.core.topic_report import TopicReport
from testudo.plugins.base import CheckStatus, CodeMetric, Severity, SeverityLevels

_STATUS_TOPIC = "/navigate_to_pose/_action/status"


def _manager(levels: dict[str, SeverityLevels] | None = None) -> SimpleNamespace:
    plugin = SimpleNamespace(levels=levels or {"stuck_count": SeverityLevels(warn=5.0, error=50.0)})
    return SimpleNamespace(
        _topic_config_by_name={"/gps": TopicConfig("/gps", "sensor_msgs/msg/NavSatFix", codes={"GNSS-007": Severity.OK})},
        _action_config_by_status_topic={_STATUS_TOPIC: ActionConfig("navigate_to_pose")},
        _msg_type_by_topic={"/gps": "sensor_msgs/msg/NavSatFix", "/imu": "sensor_msgs/msg/Imu", "/tf": "tf2_msgs/msg/TFMessage"},
        _full_tier={"/gps": SimpleNamespace(plugin=plugin, stamps=StampCheck())},
    )


def test_a_code_override_applies_to_the_declared_topic() -> None:
    manager = _manager()
    live_tuning.set_code_override(manager, "/gps", "GNSS-001", Severity.WARN)
    assert live_tuning.code_overrides(manager, "/gps") == {"GNSS-007": Severity.OK, "GNSS-001": Severity.WARN}


def test_restoring_a_code_drops_only_that_override() -> None:
    manager = _manager()
    live_tuning.set_code_override(manager, "/gps", "GNSS-001", Severity.WARN)
    live_tuning.set_code_override(manager, "/gps", "GNSS-001", None)
    assert live_tuning.code_overrides(manager, "/gps") == {"GNSS-007": Severity.OK}


def test_an_undeclared_topic_gets_a_config_of_its_own() -> None:
    manager = _manager()
    live_tuning.set_code_override(manager, "/imu", "IMU-005", Severity.OK)
    assert manager._topic_config_by_name["/imu"].msg_type == "sensor_msgs/msg/Imu"


def test_an_action_status_topic_changes_its_action() -> None:
    manager = _manager()
    live_tuning.set_code_override(manager, _STATUS_TOPIC, "ACT-001", Severity.OK)
    assert manager._action_config_by_status_topic[_STATUS_TOPIC].codes == {"ACT-001": Severity.OK}
    assert _STATUS_TOPIC not in manager._topic_config_by_name


def test_the_config_index_is_replaced_not_mutated() -> None:
    manager = _manager()
    before = manager._topic_config_by_name
    live_tuning.set_code_override(manager, "/imu", "IMU-005", Severity.OK)
    assert "/imu" not in before


def test_a_derived_row_belongs_to_its_source_topic() -> None:
    report = TopicReport("map → odom", "synthetic", "derived", CheckStatus(Severity.WARN, "l", "m"), source_topic="/tf")
    assert live_tuning.owner_topic(report) == "/tf"
    assert live_tuning.msg_type_of(_manager(), report) == "tf2_msgs/msg/TFMessage"


@pytest.mark.parametrize(
    ("value", "higher_is_worse", "expected"),
    [(12.0, True, 12.0), (0.12345, True, 0.124), (0.12345, False, 0.123), (1234.5, True, 1240.0), (0.0, True, 0.0), (0.7, False, 0.7), (0.7, True, 0.7), (-0.12345, True, -0.123)],
)
def test_the_cut_is_rounded_so_the_current_value_stays_good(value: float, higher_is_worse: bool, expected: float) -> None:
    levels = {"m": SeverityLevels()}
    metric = CodeMetric("m", value, higher_is_worse)
    assert live_tuning.warn_cut(_manager(levels), "/gps", metric) == pytest.approx(expected)


def test_no_cut_for_a_value_past_the_error_level() -> None:
    assert live_tuning.warn_cut(_manager(), "/gps", CodeMetric("stuck_count", 60.0)) is None


def test_no_cut_for_a_low_value_past_a_lower_is_worse_error_level() -> None:
    levels = {"success_rate": SeverityLevels(warn=0.9, error=0.5)}
    assert live_tuning.warn_cut(_manager(levels), "/gps", CodeMetric("success_rate", 0.4, higher_is_worse=False)) is None
    assert live_tuning.warn_cut(_manager(levels), "/gps", CodeMetric("success_rate", 0.7, higher_is_worse=False)) == 0.7


def test_the_cut_never_crosses_the_error_level() -> None:
    levels = {"m": SeverityLevels(warn=1.0, error=1.234)}
    assert live_tuning.warn_cut(_manager(levels), "/gps", CodeMetric("m", 1.2339)) == 1.234


def test_no_cut_for_a_value_that_is_not_finite() -> None:
    assert live_tuning.warn_cut(_manager(), "/gps", CodeMetric("stuck_count", float("nan"))) is None


def test_a_warn_level_reaches_the_running_plugin_and_keeps_its_error() -> None:
    manager = _manager()
    live_tuning.set_warn_level(manager, "/gps", "stuck_count", 12.0)
    assert manager._full_tier["/gps"].plugin.levels["stuck_count"] == SeverityLevels(warn=12.0, error=50.0)
    assert manager._topic_config_by_name["/gps"].levels["stuck_count"].over(SeverityLevels(error=50.0)).error == 50.0


def test_a_stamp_warn_level_reaches_the_stamp_check() -> None:
    manager = _manager()
    live_tuning.set_warn_level(manager, "/gps", STAMP_LAG_METRIC, 2.0)
    assert manager._full_tier["/gps"].stamps.lag_levels.warn == 2.0


def test_a_rate_warn_level_keeps_a_written_error() -> None:
    manager = _manager()
    manager._topic_config_by_name = {
        "/gps": TopicConfig("/gps", "t", levels={"rate_hz": LevelsOverride(warn=9.0, error=2.0, given=frozenset({"warn", "error"}))})
    }
    live_tuning.set_warn_level(manager, "/gps", "rate_hz", 5.0)
    assert manager._topic_config_by_name["/gps"].levels["rate_hz"].over(None) == SeverityLevels(warn=5.0, error=2.0)


def _nav2_manager(config: TopicConfig | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        _topic_config_by_name={"/cmd_vel_nav": config} if config is not None else {},
        _full_tier={},
        _nav2_rates=SimpleNamespace(expected={"/cmd_vel_nav": object()}),
    )


def test_a_rate_judged_against_nav2_is_not_offered_a_warn_level() -> None:
    metric = CodeMetric("rate_hz", 15.0, higher_is_worse=False)
    assert live_tuning.warn_cut(_nav2_manager(), "/cmd_vel_nav", metric) is None


def test_a_rate_the_config_already_sets_can_still_take_one() -> None:
    written = LevelsOverride(warn=18.0, given=frozenset({"warn"}))
    config = TopicConfig("/cmd_vel_nav", "geometry_msgs/msg/Twist", levels={"rate_hz": written})
    metric = CodeMetric("rate_hz", 15.0, higher_is_worse=False)
    assert live_tuning.warn_cut(_nav2_manager(config), "/cmd_vel_nav", metric) == 15.0
