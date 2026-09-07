"""Unit tests for graph discovery, using duck-typed stand-ins for rclpy.Node.

No live ROS graph needed: `list_topics`/`list_actions` only call
`get_topic_names_and_types`, and `qos_from_publisher_infos` is a pure
function split out specifically so QoS matching is testable in isolation.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest
from rclpy.qos import QoSProfile

from testudo.core.discovery import (
    NoPublishersError,
    fully_qualified_node_name,
    list_actions,
    list_lifecycle_nodes,
    list_topics,
    qos_from_publisher_infos,
    resolve_publisher_info,
    resolve_subscription_qos,
)


class _FakeNode:
    def __init__(self, topics: list[tuple[str, list[str]]], publishers_by_topic: dict[str, list[object]]):
        self._topics = topics
        self._publishers_by_topic = publishers_by_topic

    def get_topic_names_and_types(self) -> list[tuple[str, list[str]]]:
        return self._topics

    def get_publishers_info_by_topic(self, topic_name: str) -> list[object]:
        return self._publishers_by_topic.get(topic_name, [])


@dataclass
class _FakeEndpointInfo:
    qos_profile: QoSProfile
    node_name: str = "some_node"
    node_namespace: str = "/"


def test_list_topics_wraps_node_query() -> None:
    node = _FakeNode(
        topics=[("/odom", ["nav_msgs/msg/Odometry"]), ("/scan", ["sensor_msgs/msg/LaserScan"])],
        publishers_by_topic={},
    )
    topics = list_topics(node)
    assert {t.name for t in topics} == {"/odom", "/scan"}
    odom = next(t for t in topics if t.name == "/odom")
    assert odom.msg_types == ("nav_msgs/msg/Odometry",)


def test_list_actions_finds_status_topics() -> None:
    node = _FakeNode(
        topics=[
            ("/navigate_to_pose/_action/status", ["action_msgs/msg/GoalStatusArray"]),
            ("/odom", ["nav_msgs/msg/Odometry"]),
        ],
        publishers_by_topic={},
    )
    actions = list_actions(node)
    assert [a.name for a in actions] == ["/navigate_to_pose"]


def test_qos_from_publisher_infos_picks_first_publisher() -> None:
    qos = QoSProfile(depth=10)
    infos = [_FakeEndpointInfo(qos_profile=qos)]
    result = qos_from_publisher_infos(infos, "/odom")
    assert result is qos


def test_qos_from_publisher_infos_raises_on_no_publishers() -> None:
    with pytest.raises(NoPublishersError, match="/odom"):
        qos_from_publisher_infos([], "/odom")


def test_resolve_subscription_qos_uses_node_query() -> None:
    qos = QoSProfile(depth=5)
    node = _FakeNode(topics=[], publishers_by_topic={"/scan": [_FakeEndpointInfo(qos_profile=qos)]})
    assert resolve_subscription_qos(node, "/scan") is qos


def test_resolve_subscription_qos_raises_on_no_publishers() -> None:
    node = _FakeNode(topics=[], publishers_by_topic={})
    with pytest.raises(NoPublishersError):
        resolve_subscription_qos(node, "/unpublished")


def test_list_lifecycle_nodes_finds_transition_event_topics() -> None:
    node = _FakeNode(
        topics=[
            ("/controller_server/transition_event", ["lifecycle_msgs/msg/TransitionEvent"]),
            ("/odom", ["nav_msgs/msg/Odometry"]),
        ],
        publishers_by_topic={},
    )
    lifecycle_nodes = list_lifecycle_nodes(node)
    assert [n.node_name for n in lifecycle_nodes] == ["/controller_server"]


def test_resolve_publisher_info_returns_full_endpoint_info() -> None:
    info = _FakeEndpointInfo(qos_profile=QoSProfile(depth=3), node_name="talker", node_namespace="/ns")
    node = _FakeNode(topics=[], publishers_by_topic={"/chatter": [info]})
    result = resolve_publisher_info(node, "/chatter")
    assert result is info


def test_resolve_publisher_info_raises_on_no_publishers() -> None:
    node = _FakeNode(topics=[], publishers_by_topic={})
    with pytest.raises(NoPublishersError):
        resolve_publisher_info(node, "/unpublished")


def test_fully_qualified_node_name_combines_namespace_and_name() -> None:
    assert fully_qualified_node_name("/", "talker") == "/talker"
    assert fully_qualified_node_name("/ns", "talker") == "/ns/talker"
    assert fully_qualified_node_name("/ns/", "talker") == "/ns/talker"
    assert fully_qualified_node_name("", "talker") == "/talker"
