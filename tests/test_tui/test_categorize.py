"""Unit tests for category derivation -- pure function, no ROS/Textual needed."""
from __future__ import annotations

from testudo.core.topic_report import TopicReport
from testudo.plugins.base import CheckStatus, Severity
from testudo.tui.categorize import OTHER_CATEGORY, category_for


def _report(msg_type: str, tier: str) -> TopicReport:
    return TopicReport(topic="/x", msg_type=msg_type, tier=tier, status=CheckStatus(Severity.OK, "l", "m"))


def test_vitals_tier_always_goes_to_other_category() -> None:
    assert category_for(_report("nav_msgs/msg/Odometry", "vitals")) == OTHER_CATEGORY
    assert category_for(_report("sensor_msgs/msg/LaserScan", "vitals")) == OTHER_CATEGORY


def test_full_tier_uses_message_type_short_name() -> None:
    assert category_for(_report("nav_msgs/msg/Odometry", "full")) == "Odometry"
    assert category_for(_report("sensor_msgs/msg/Imu", "full")) == "Imu"


def test_full_tier_friendly_name_overrides() -> None:
    assert category_for(_report("action_msgs/msg/GoalStatusArray", "full")) == "Nav2 Actions"
    assert category_for(_report("tf2_msgs/msg/TFMessage", "full")) == "TF"


def test_unknown_full_tier_type_falls_back_to_short_name() -> None:
    assert category_for(_report("my_pkg/msg/CustomThing", "full")) == "CustomThing"
