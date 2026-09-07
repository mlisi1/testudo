"""Integration tests for bag replay -- writes small real bags with rosbag2_py,
then replays them through the actual pipeline. Needs a live rclpy node (the
same way `SubscriptionManager` always does) but no live ROS graph/publishers.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import rclpy
import rosbag2_py
from geometry_msgs.msg import TransformStamped
from nav_msgs.msg import Odometry
from rclpy.serialization import serialize_message
from std_msgs.msg import String
from tf2_msgs.msg import TFMessage

from testudo.core.config import PublishConfig, SeverityMode, TestudoConfig
from testudo.core.replay import replay_bag
from testudo.plugins.base import Severity
from testudo.plugins.registry import discover_all_plugins


@pytest.fixture(scope="module")
def ros_node():
    rclpy.init()
    node = rclpy.create_node("test_replay_node")
    yield node
    node.destroy_node()
    rclpy.shutdown()


def _write_bag(bag_path: Path, entries: list[tuple[str, str, object, int]]) -> None:
    """entries: (topic_name, msg_type_str, message, timestamp_ns), one create_topic per unique topic."""
    writer = rosbag2_py.SequentialWriter()
    storage_options = rosbag2_py.StorageOptions(uri=str(bag_path), storage_id="mcap")
    converter_options = rosbag2_py.ConverterOptions(input_serialization_format="cdr", output_serialization_format="cdr")
    writer.open(storage_options, converter_options)

    topic_types: dict[str, str] = {}
    for topic_name, msg_type_str, _msg, _stamp in entries:
        topic_types.setdefault(topic_name, msg_type_str)

    for topic_id, (topic_name, msg_type_str) in enumerate(topic_types.items()):
        writer.create_topic(
            rosbag2_py.TopicMetadata(id=topic_id, name=topic_name, type=msg_type_str, serialization_format="cdr")
        )

    for topic_name, _msg_type_str, msg, stamp in entries:
        writer.write(topic_name, serialize_message(msg), stamp)
    del writer


def _config(**overrides) -> TestudoConfig:
    return TestudoConfig(publish=PublishConfig(topic="/diagnostics"), **overrides)


def _plugins():
    return discover_all_plugins(None)


def test_replay_vitals_only_topic(tmp_path: Path, ros_node) -> None:
    bag_path = tmp_path / "bag1"
    msg = String(data="hello")
    _write_bag(bag_path, [("/chatter", "std_msgs/msg/String", msg, 1_000_000_000)])

    reports, overall = replay_bag(str(bag_path), _config(), _plugins(), ros_node)

    assert [r.topic for r in reports] == ["/chatter"]
    assert reports[0].tier == "vitals"
    assert reports[0].status.severity == Severity.OK


def test_replay_full_tier_topic_detects_content_problem(tmp_path: Path, ros_node) -> None:
    bag_path = tmp_path / "bag2"
    odom = Odometry()
    odom.header.stamp.sec = 1
    cov = [0.0] * 36
    cov[0] = -1.0  # negative variance -- fails the PSD check
    odom.pose.covariance = cov
    _write_bag(bag_path, [("/odom", "nav_msgs/msg/Odometry", odom, 1_000_000_000)])

    reports, overall = replay_bag(str(bag_path), _config(), _plugins(), ros_node)

    assert reports[0].tier == "full"
    assert reports[0].status.severity == Severity.ERROR
    assert "positive semi-definite" in reports[0].status.message


def test_replay_tf_static_routes_to_same_instance_as_tf(tmp_path: Path, ros_node) -> None:
    bag_path = tmp_path / "bag3"

    static_transform = TransformStamped()
    static_transform.header.frame_id = "map"
    static_transform.child_frame_id = "odom"

    dynamic_transform = TransformStamped()
    dynamic_transform.header.frame_id = "odom"
    dynamic_transform.child_frame_id = "base_link"

    # A second, bogus parent for base_link -- deliberate multi-parent bug.
    bogus_transform = TransformStamped()
    bogus_transform.header.frame_id = "map"
    bogus_transform.child_frame_id = "base_link"

    entries = [
        ("/tf_static", "tf2_msgs/msg/TFMessage", TFMessage(transforms=[static_transform]), 1_000_000_000),
        ("/tf", "tf2_msgs/msg/TFMessage", TFMessage(transforms=[dynamic_transform]), 2_000_000_000),
        # 3 consecutive bogus messages: HysteresisDebouncer needs 3 consecutive
        # samples at a new severity before it transitions (its default), so a
        # single bad reading here would correctly stay hidden -- that's not
        # a bug, it's the same debounce a live check would apply too.
        ("/tf", "tf2_msgs/msg/TFMessage", TFMessage(transforms=[bogus_transform]), 3_000_000_000),
        ("/tf", "tf2_msgs/msg/TFMessage", TFMessage(transforms=[bogus_transform]), 4_000_000_000),
        ("/tf", "tf2_msgs/msg/TFMessage", TFMessage(transforms=[bogus_transform]), 5_000_000_000),
    ]
    _write_bag(bag_path, entries)

    reports, overall = replay_bag(str(bag_path), _config(), _plugins(), ros_node)

    # /tf_static never gets its own report row -- it fed the /tf plugin instance.
    assert [r.topic for r in reports] == ["/tf"]
    assert reports[0].status.severity == Severity.ERROR
    assert "multi-parent" in reports[0].status.message
    # 3 unique edges (map->odom, odom->base_link, map->base_link) -- edge_count
    # is unique edges, not messages processed -- includes the static one,
    # proving it was actually merged into the same instance as /tf.
    assert int(reports[0].status.values["edge_count"]) == 3


def test_replay_stale_topic_relative_to_bag_end_time(tmp_path: Path, ros_node) -> None:
    bag_path = tmp_path / "bag4"
    entries = [
        ("/chatter", "std_msgs/msg/String", String(data="a"), 0),
        # A second, unrelated topic keeps the bag "running" long after /chatter went silent.
        ("/other", "std_msgs/msg/String", String(data="b"), 100_000_000_000),  # 100s later
    ]
    _write_bag(bag_path, entries)

    reports, overall = replay_bag(str(bag_path), _config(), _plugins(), ros_node)
    chatter = next(r for r in reports if r.topic == "/chatter")
    assert chatter.status.severity == Severity.STALE


def test_replay_missing_bag_raises_file_not_found(tmp_path: Path, ros_node) -> None:
    with pytest.raises(FileNotFoundError):
        replay_bag(str(tmp_path / "does_not_exist"), _config(), _plugins(), ros_node)


def test_replay_overall_status_uses_configured_severity_mode(tmp_path: Path, ros_node) -> None:
    bag_path = tmp_path / "bag5"
    odom = Odometry()
    cov = [0.0] * 36
    cov[0] = -1.0
    odom.pose.covariance = cov
    _write_bag(bag_path, [("/odom", "nav_msgs/msg/Odometry", odom, 1_000_000_000)])

    reports, overall = replay_bag(
        str(bag_path), _config(severity_mode=SeverityMode.WORST), _plugins(), ros_node
    )
    assert overall.severity == Severity.ERROR
    assert overall.label == "overall"
