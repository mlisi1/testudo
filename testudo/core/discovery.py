"""ROS graph introspection: topic/action enumeration and QoS auto-matching.

Query `get_publishers_info_by_topic` before subscribing to any topic so
Testudo matches the offered QoS instead of defaulting and silently failing on
incompatibility. The same call flags zero-publisher topics immediately.
"""
from __future__ import annotations

from dataclasses import dataclass

import rclpy.node
from rclpy.qos import QoSProfile
from rclpy.topic_endpoint_info import TopicEndpointInfo

ACTION_STATUS_SUFFIX = "/_action/status"
LIFECYCLE_TRANSITION_EVENT_SUFFIX = "/transition_event"


@dataclass(frozen=True)
class TopicInfo:
    """One topic on the graph and the message type(s) publishers offer for it."""

    name: str
    msg_types: tuple[str, ...]


def list_topics(node: rclpy.node.Node) -> list[TopicInfo]:
    """Enumerate every topic currently visible on the ROS graph."""
    return [TopicInfo(name=name, msg_types=tuple(types)) for name, types in node.get_topic_names_and_types()]


@dataclass(frozen=True)
class ActionInfo:
    """One action server on the graph, identified by its status topic."""

    name: str


def list_actions(node: rclpy.node.Node) -> list[ActionInfo]:
    """Enumerate action servers visible on the ROS graph.

    rclpy has no direct action-introspection API, so actions are discovered
    by the presence of their `<name>/_action/status` status topic.
    """
    actions = []
    for topic_name, _types in node.get_topic_names_and_types():
        if topic_name.endswith(ACTION_STATUS_SUFFIX):
            action_name = topic_name[: -len(ACTION_STATUS_SUFFIX)]
            actions.append(ActionInfo(name=action_name))
    return actions


@dataclass(frozen=True)
class LifecycleNodeInfo:
    """One lifecycle node on the graph, identified by its transition-event topic."""

    node_name: str  # fully-qualified, e.g. "/controller_server"


def list_lifecycle_nodes(node: rclpy.node.Node) -> list[LifecycleNodeInfo]:
    """Enumerate lifecycle nodes visible on the ROS graph.

    Every `rclpy_lifecycle`/`rclcpp_lifecycle` node automatically publishes
    `<node>/transition_event` (lifecycle_msgs/msg/TransitionEvent), so -- the
    same trick as `list_actions` -- lifecycle nodes are discovered by that
    topic's presence rather than by querying each node's lifecycle service.
    """
    return [
        LifecycleNodeInfo(node_name=topic_name[: -len(LIFECYCLE_TRANSITION_EVENT_SUFFIX)])
        for topic_name, _types in node.get_topic_names_and_types()
        if topic_name.endswith(LIFECYCLE_TRANSITION_EVENT_SUFFIX)
    ]


def fully_qualified_node_name(node_namespace: str, node_name: str) -> str:
    """Combine a publisher's namespace + name (from TopicEndpointInfo) into one `/ns/node` string.

    Matches the format `list_lifecycle_nodes`/`list_actions` derive from a
    topic name's prefix, so the two can be compared directly.
    """
    namespace = node_namespace.rstrip("/")
    return f"{namespace}/{node_name}" if namespace else f"/{node_name}"


class NoPublishersError(Exception):
    """Raised when a topic has zero publishers at query time."""


def resolve_subscription_qos(node: rclpy.node.Node, topic_name: str) -> QoSProfile:
    """Return a QoS profile compatible with `topic_name`'s current publisher(s).

    Raises:
        NoPublishersError: the topic currently has no publishers, so callers
            can flag it immediately rather than silently failing to receive data.
    """
    return resolve_publisher_info(node, topic_name).qos_profile


def resolve_publisher_info(node: rclpy.node.Node, topic_name: str) -> TopicEndpointInfo:
    """Return the publisher endpoint info Testudo subscribes against for `topic_name`.

    Carries more than QoS -- notably `node_name`/`node_namespace`, used to
    attribute a topic to its owning node for lifecycle-aware suppression.

    Raises:
        NoPublishersError: the topic currently has no publishers, so callers
            can flag it immediately rather than silently failing to receive data.
    """
    infos = node.get_publishers_info_by_topic(topic_name)
    return first_publisher_info(infos, topic_name)


def qos_from_publisher_infos(infos: list[TopicEndpointInfo], topic_name: str) -> QoSProfile:
    """Pick a subscription QoS profile from publisher endpoint infos.

    Split out from `resolve_subscription_qos` so the matching logic is
    unit-testable with synthetic endpoint info, without a live ROS graph.
    """
    return first_publisher_info(infos, topic_name).qos_profile


def first_publisher_info(infos: list[TopicEndpointInfo], topic_name: str) -> TopicEndpointInfo:
    """Pick the publisher endpoint info to use for a subscription.

    Split out so it's unit-testable with synthetic endpoint info, without a
    live ROS graph. Matches the first publisher found; a topic with
    multiple publishers offering incompatible QoS is a robot-config problem
    outside Testudo's control.
    """
    if not infos:
        raise NoPublishersError(f"topic '{topic_name}' currently has no publishers")
    return infos[0]
