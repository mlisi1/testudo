"""Unit tests for category derivation -- pure function, no ROS/Textual needed."""
from __future__ import annotations

from testudo.core.topic_report import TopicReport
from testudo.plugins.base import CheckStatus, Severity
from testudo.tui.categorize import EXCLUDED_CATEGORY, OTHER_CATEGORY, category_for


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


def test_presence_tier_gets_its_own_category_not_other() -> None:
    """A presence-only topic still has a plugin identity -- it shouldn't disappear into Other Topics."""
    assert category_for(_report("sensor_msgs/msg/Image", "presence")) == "ImageStream"
    assert category_for(_report("sensor_msgs/msg/PointCloud2", "presence")) == "PointStream"


def test_presence_tier_unknown_type_falls_back_to_short_name() -> None:
    assert category_for(_report("my_pkg/msg/CustomBigThing", "presence")) == "CustomBigThing"


def test_point_stream_friendly_name_groups_2d_and_3d_together() -> None:
    assert category_for(_report("sensor_msgs/msg/LaserScan", "full")) == "PointStream"
    assert category_for(_report("sensor_msgs/msg/PointCloud2", "presence")) == "PointStream"


def test_theora_packet_groups_with_image_stream() -> None:
    """No plugin covers theora packets, but they're still a camera's frames -- group with ImageStream, not alone."""
    assert category_for(_report("theora_image_transport/msg/Packet", "presence")) == "ImageStream"


def test_excluded_tier_always_goes_to_its_own_category_regardless_of_msg_type() -> None:
    """A user exclude rule can match any message type -- they all land in one shared bucket."""
    assert category_for(_report("sensor_msgs/msg/PointCloud2", "excluded")) == EXCLUDED_CATEGORY
    assert category_for(_report("nav_msgs/msg/Odometry", "excluded")) == EXCLUDED_CATEGORY
