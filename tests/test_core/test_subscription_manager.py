"""Unit tests for SubscriptionManager's tier-assignment and reporting logic.

Uses a fake rclpy node (duck-typed: `get_topic_names_and_types`,
`get_publishers_info_by_topic`, `create_subscription`) so tier assignment,
exclusion, and report generation are all testable without a live ROS graph.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from rclpy.qos import DurabilityPolicy, QoSProfile

import pytest

from testudo.core.clock import TestudoClock
from testudo.core.config import (
    ActionConfig,
    ExcludeMatchType,
    ExcludeRule,
    LevelsOverride,
    PublishConfig,
    SeverityMode,
    TestudoConfig,
    TFPairConfig,
    TopicConfig,
)
from testudo.core.nav2_rates import ExpectedRate
from testudo.core.report_builder import build_reports
from testudo.core.subscription_manager import SubscriptionManager
from testudo.core.topic_report import TopicReport
from testudo.plugins.base import CheckPlugin, CheckStatus, Severity, ThresholdZone
from testudo.plugins.registry import DiscoveredPlugin


@dataclass
class _FakeEndpointInfo:
    qos_profile: QoSProfile
    node_name: str = "some_node"
    node_namespace: str = "/"


@dataclass
class _RecordedSubscription:
    msg_class: type
    topic: str
    callback: Callable[[Any], None]
    qos_profile: QoSProfile
    raw: bool


@dataclass
class _FakeLifecycleState:
    label: str


@dataclass
class _FakeGetStateResponse:
    current_state: _FakeLifecycleState


class _FakeFuture:
    """A `~/get_state` future that's already resolved by the time it's created."""

    def __init__(self, result: object | None) -> None:
        self._result = result

    def done(self) -> bool:
        return True

    def result(self) -> object | None:
        return self._result


class _FakeGetStateClient:
    def __init__(self, response_label: str | None) -> None:
        self._response_label = response_label

    def service_is_ready(self) -> bool:
        return self._response_label is not None

    def call_async(self, request: object) -> _FakeFuture:
        if self._response_label is None:
            return _FakeFuture(None)
        return _FakeFuture(_FakeGetStateResponse(_FakeLifecycleState(self._response_label)))


class _FakeNode:
    def __init__(
        self,
        topics: list[tuple[str, list[str]]],
        publishers_by_topic: dict[str, list[object]],
        get_state_responses: dict[str, str] | None = None,
    ):
        self._topics = topics
        self._publishers_by_topic = publishers_by_topic
        self._get_state_responses = get_state_responses or {}
        self.subscriptions: list[_RecordedSubscription] = []

    def get_topic_names_and_types(self) -> list[tuple[str, list[str]]]:
        return self._topics

    def get_node_names_and_namespaces(self) -> list[tuple[str, str]]:
        return []

    def get_publishers_info_by_topic(self, topic_name: str) -> list[object]:
        return self._publishers_by_topic.get(topic_name, [])

    def get_subscriptions_info_by_topic(self, topic_name: str) -> list[object]:
        return [_FakeEndpointInfo(qos_profile=_qos(), node_name="bt_navigator")]

    def create_subscription(self, msg_class, topic, callback, qos_profile, raw: bool = False):
        sub = _RecordedSubscription(msg_class, topic, callback, qos_profile, raw)
        self.subscriptions.append(sub)
        return sub

    def create_client(self, srv_type: type, srv_name: str) -> _FakeGetStateClient:
        node_name = srv_name.removesuffix("/get_state")
        return _FakeGetStateClient(self._get_state_responses.get(node_name))

    def destroy_client(self, client: object) -> None:
        pass

    def destroy_subscription(self, subscription: object) -> None:
        self.subscriptions.remove(subscription)


def _fixed_clock(seconds: float) -> TestudoClock:
    class _Fixed:
        def now(self):
            class _T:
                nanoseconds = int(seconds * 1e9)

            return _T()

    return TestudoClock(_Fixed(), sim_time_query=lambda: False)


class _EmptyPlugin(CheckPlugin):
    """Test-only plugin covering std_msgs/msg/Empty, mirroring DummyPlugin's shape."""

    def __init__(self, thresholds=None, related_topics=None) -> None:
        super().__init__(thresholds, related_topics)
        self.messages: list[Any] = []

    @classmethod
    def msg_types(cls) -> tuple[str, ...]:
        return ("std_msgs/msg/Empty",)

    def on_message(self, topic: str, msg: Any) -> None:
        self.messages.append(msg)

    def get_status(self) -> CheckStatus:
        return CheckStatus(severity=Severity.OK, label="empty", message=f"{len(self.messages)} msg(s)")


class _ImagePlugin(CheckPlugin):
    """Test-only plugin covering sensor_msgs/msg/Image, mirroring ImageStreamPlugin's shape."""

    def __init__(self, thresholds=None, related_topics=None) -> None:
        super().__init__(thresholds, related_topics)
        self.messages: list[Any] = []

    @classmethod
    def msg_types(cls) -> tuple[str, ...]:
        return ("sensor_msgs/msg/Image",)

    @classmethod
    def presence_only_msg_types(cls) -> frozenset[str]:
        return frozenset({"sensor_msgs/msg/Image"})

    def on_message(self, topic: str, msg: Any) -> None:
        self.messages.append(msg)

    def get_status(self) -> CheckStatus:
        return CheckStatus(severity=Severity.OK, label="image", message=f"{len(self.messages)} msg(s)")


class _RecordingPlugin(CheckPlugin):
    """Test-only plugin that records every `on_message` call and reports whatever severity is queued."""

    def __init__(self, thresholds=None, related_topics=None) -> None:
        super().__init__(thresholds, related_topics)
        self.calls: list[tuple[str, Any]] = []
        self.next_severity = Severity.OK

    @classmethod
    def msg_types(cls) -> tuple[str, ...]:
        return ("nav_msgs/msg/Odometry",)

    def on_message(self, topic: str, msg: Any) -> None:
        self.calls.append((topic, msg))

    def get_status(self) -> CheckStatus:
        return CheckStatus(severity=self.next_severity, label="recording", message="synthetic")


class _ActionPlugin(CheckPlugin):
    """Test-only plugin covering action_msgs/msg/GoalStatusArray, mirroring ActionsPlugin's shape."""

    def __init__(self, thresholds=None, related_topics=None) -> None:
        super().__init__(thresholds, related_topics)
        self.calls: list[tuple[str, Any]] = []

    @classmethod
    def msg_types(cls) -> tuple[str, ...]:
        return ("action_msgs/msg/GoalStatusArray",)

    def on_message(self, topic: str, msg: Any) -> None:
        self.calls.append((topic, msg))

    def get_status(self) -> CheckStatus:
        return CheckStatus(severity=Severity.OK, label="action", message=f"{len(self.calls)} update(s)")


class _TFPlugin(CheckPlugin):
    """Test-only plugin covering tf2_msgs/msg/TFMessage, mirroring TFWatchPlugin's shape."""

    def __init__(self, thresholds=None, related_topics=None) -> None:
        super().__init__(thresholds, related_topics)
        self.calls: list[tuple[str, Any]] = []
        self.watched_pairs: list[tuple[str, str]] | None = None
        self.tick_values: list[float] = []

    @classmethod
    def msg_types(cls) -> tuple[str, ...]:
        return ("tf2_msgs/msg/TFMessage",)

    def set_watched_pairs(self, pairs: list[tuple[str, str]]) -> None:
        self.watched_pairs = list(pairs)

    def on_tick(self, now_seconds: float) -> None:
        self.tick_values.append(now_seconds)

    def on_message(self, topic: str, msg: Any) -> None:
        self.calls.append((topic, msg))

    def get_status(self) -> CheckStatus:
        return CheckStatus(severity=Severity.OK, label="tf", message=f"{len(self.calls)} update(s)")


def _config(publish_topic: str = "/diagnostics") -> TestudoConfig:
    return TestudoConfig(publish=PublishConfig(topic=publish_topic))


def _declaring(*topics: tuple[str, str]) -> TestudoConfig:
    """A config declaring each (topic, message type): their silence is an error."""
    declared: dict[str, list[TopicConfig]] = {}
    for name, msg_type in topics:
        declared.setdefault(msg_type, []).append(TopicConfig(name=name, msg_type=msg_type))
    return TestudoConfig(topics=declared, publish=PublishConfig(topic="/diagnostics"))


def _qos() -> QoSProfile:
    return QoSProfile(depth=10)


# The fake node's topic list is static and needs no real discovery wait,
# its `~/get_state` clients report readiness instantly, and its futures are
# already resolved when created, needing no real spin -- keep all three
# hermetic (and the timeout tiny, so a genuinely-unavailable fake service
# doesn't busy-loop for the real production budget) so tests avoid
# production ROS I/O and stay fast.
_FAST_SETTLE = {
    "graph_settle_seconds": 0.0,
    "get_state_timeout_seconds": 0.01,
    "spin_once": lambda node, timeout_sec=None: None,
}


def test_default_excludes_and_self_exclusion_are_not_subscribed() -> None:
    node = _FakeNode(
        topics=[
            ("/parameter_events", ["rcl_interfaces/msg/ParameterEvent"]),
            ("/rosout", ["rcl_interfaces/msg/Log"]),
            ("/diagnostics", ["diagnostic_msgs/msg/DiagnosticArray"]),
            ("/odom", ["nav_msgs/msg/Odometry"]),
        ],
        publishers_by_topic={"/odom": [_FakeEndpointInfo(qos_profile=_qos())]},
    )
    manager = SubscriptionManager(node, _config(), [], clock=_fixed_clock(0.0), **_FAST_SETTLE)
    manager.start()

    subscribed_topics = {sub.topic for sub in node.subscriptions}
    assert subscribed_topics == {"/odom"}


def test_topic_with_no_covering_plugin_gets_vitals_tier() -> None:
    node = _FakeNode(
        topics=[("/odom", ["nav_msgs/msg/Odometry"])],
        publishers_by_topic={"/odom": [_FakeEndpointInfo(qos_profile=_qos())]},
    )
    manager = SubscriptionManager(node, _config(), [], clock=_fixed_clock(0.0), **_FAST_SETTLE)
    manager.start()

    assert len(node.subscriptions) == 1
    sub = node.subscriptions[0]
    assert sub.topic == "/odom"
    assert sub.raw is True


def test_topic_with_covering_plugin_gets_full_tier() -> None:
    node = _FakeNode(
        topics=[("/beep", ["std_msgs/msg/Empty"])],
        publishers_by_topic={"/beep": [_FakeEndpointInfo(qos_profile=_qos())]},
    )
    discovered = [DiscoveredPlugin(name="empty", plugin_class=_EmptyPlugin, source="packaged")]
    manager = SubscriptionManager(node, _config(), discovered, clock=_fixed_clock(0.0), **_FAST_SETTLE)
    manager.start()

    assert len(node.subscriptions) == 1
    sub = node.subscriptions[0]
    assert sub.topic == "/beep"
    assert sub.raw is False

    # A full-tier topic still needs at least one message before it's "alive" --
    # liveness is tracked for full tier the same as vitals tier.
    sub.callback(object())

    reports = manager.reports()
    assert len(reports) == 1
    assert reports[0].tier == "full"
    assert reports[0].status.severity == Severity.OK


def test_undeclared_image_topic_gets_presence_tier_without_subscribing() -> None:
    """An undeclared sensor_msgs/msg/Image topic isn't subscribed at all, even with a covering plugin.

    Even a raw vitals subscription still costs whatever CycloneDDS/RTPS
    charges to transport the payload -- image data is heavy enough that
    auto-watching every camera topic on a real robot measurably starves
    other nodes (see DEFAULT_PRESENCE_ONLY_MSG_TYPES). Presence (a
    publisher exists) is still reported; declaring the topic under
    `topics:` opts it back into full monitoring.
    """
    node = _FakeNode(
        topics=[("/camera/image_raw", ["sensor_msgs/msg/Image"])],
        publishers_by_topic={"/camera/image_raw": [_FakeEndpointInfo(qos_profile=_qos())]},
    )
    discovered = [DiscoveredPlugin(name="image", plugin_class=_ImagePlugin, source="packaged")]
    manager = SubscriptionManager(node, _config(), discovered, clock=_fixed_clock(0.0), **_FAST_SETTLE)
    manager.start()

    assert node.subscriptions == []
    reports = manager.reports()
    assert len(reports) == 1
    assert reports[0].topic == "/camera/image_raw"
    assert reports[0].tier == "presence"
    assert reports[0].status.severity == Severity.OK
    assert "not subscribed" in reports[0].status.message


def test_undeclared_theora_topic_gets_presence_tier_without_subscribing() -> None:
    """A lazy image_transport republisher (theora) shouldn't be woken up by an undeclared subscription.

    `image_transport`'s compressed/theora republishers only run their
    encoder once something subscribes -- observed in the wild waking up a
    misconfigured depth-image theora republisher into erroring on every
    frame, purely because Testudo subscribed to it for vitals.
    """
    node = _FakeNode(
        topics=[("/camera/image/theora", ["theora_image_transport/msg/Packet"])],
        publishers_by_topic={"/camera/image/theora": [_FakeEndpointInfo(qos_profile=_qos())]},
    )
    manager = SubscriptionManager(node, _config(), [], clock=_fixed_clock(0.0), **_FAST_SETTLE)
    manager.start()

    assert node.subscriptions == []
    reports = manager.reports()
    assert reports[0].tier == "presence"


def test_a_plugin_can_default_only_some_of_its_own_msg_types_to_presence() -> None:
    """A plugin covering two types (like PointStreamPlugin's LaserScan+PointCloud2) can tier them differently."""

    class _MixedTierPlugin(CheckPlugin):
        @classmethod
        def msg_types(cls) -> tuple[str, ...]:
            return ("sensor_msgs/msg/LaserScan", "sensor_msgs/msg/PointCloud2")

        @classmethod
        def presence_only_msg_types(cls) -> frozenset[str]:
            return frozenset({"sensor_msgs/msg/PointCloud2"})

        def on_message(self, topic: str, msg: Any) -> None:
            pass

        def get_status(self) -> CheckStatus:
            return CheckStatus(severity=Severity.OK, label="mixed", message="ok")

    node = _FakeNode(
        topics=[("/scan", ["sensor_msgs/msg/LaserScan"]), ("/points", ["sensor_msgs/msg/PointCloud2"])],
        publishers_by_topic={
            "/scan": [_FakeEndpointInfo(qos_profile=_qos())],
            "/points": [_FakeEndpointInfo(qos_profile=_qos())],
        },
    )
    discovered = [DiscoveredPlugin(name="mixed", plugin_class=_MixedTierPlugin, source="packaged")]
    manager = SubscriptionManager(node, _config(), discovered, clock=_fixed_clock(0.0), **_FAST_SETTLE)
    manager.start()

    subscribed_topics = {sub.topic for sub in node.subscriptions}
    assert subscribed_topics == {"/scan"}
    reports_by_topic = {report.topic: report.tier for report in manager.reports()}
    assert reports_by_topic == {"/scan": "full", "/points": "presence"}


def test_declared_image_topic_still_gets_full_tier() -> None:
    node = _FakeNode(
        topics=[("/camera/image_raw", ["sensor_msgs/msg/Image"])],
        publishers_by_topic={"/camera/image_raw": [_FakeEndpointInfo(qos_profile=_qos())]},
    )
    discovered = [DiscoveredPlugin(name="image", plugin_class=_ImagePlugin, source="packaged")]
    config = TestudoConfig(
        topics={"sensor_msgs/msg/Image": [TopicConfig(name="/camera/image_raw", msg_type="sensor_msgs/msg/Image")]}
    )
    manager = SubscriptionManager(node, config, discovered, clock=_fixed_clock(0.0), **_FAST_SETTLE)
    manager.start()

    assert len(node.subscriptions) == 1
    assert node.subscriptions[0].topic == "/camera/image_raw"
    reports = manager.reports()
    assert reports[0].tier == "full"


def test_exclude_topics_regex_pattern_gets_an_excluded_report_not_a_subscription() -> None:
    node = _FakeNode(
        topics=[("/heavy/points", ["sensor_msgs/msg/PointCloud2"]), ("/odom", ["nav_msgs/msg/Odometry"])],
        publishers_by_topic={
            "/heavy/points": [_FakeEndpointInfo(qos_profile=_qos())],
            "/odom": [_FakeEndpointInfo(qos_profile=_qos())],
        },
    )
    config = TestudoConfig(exclude_topics=[ExcludeRule("^/heavy/", ExcludeMatchType.REGEX)])
    manager = SubscriptionManager(node, config, [], clock=_fixed_clock(0.0), **_FAST_SETTLE)
    manager.start()

    subscribed_topics = {sub.topic for sub in node.subscriptions}
    assert subscribed_topics == {"/odom"}
    reports_by_topic = {report.topic: report for report in manager.reports()}
    assert set(reports_by_topic) == {"/odom", "/heavy/points"}
    excluded = reports_by_topic["/heavy/points"]
    assert excluded.tier == "excluded"
    assert excluded.status.severity == Severity.OK
    assert excluded.status.values == {"excluded_by_rule": "regex ^/heavy/"}
    assert excluded.publishers == ()


def test_reports_carry_every_publisher_seen_at_discovery() -> None:
    node = _FakeNode(
        topics=[("/cmd_vel", ["geometry_msgs/msg/Twist"])],
        publishers_by_topic={
            "/cmd_vel": [
                _FakeEndpointInfo(qos_profile=_qos(), node_name="controller_server"),
                _FakeEndpointInfo(qos_profile=_qos(), node_name="teleop", node_namespace="/joy"),
            ]
        },
    )
    manager = SubscriptionManager(node, _config(), [], clock=_fixed_clock(0.0), **_FAST_SETTLE)
    manager.start()

    publishers = manager.reports()[0].publishers
    assert [publisher.split(" ")[0] for publisher in publishers] == ["/controller_server", "/joy/teleop"]


def test_default_excluded_topics_never_get_an_excluded_report() -> None:
    """ROS graph bookkeeping (/parameter_events, /rosout, self) stays fully invisible, unlike a user rule."""
    node = _FakeNode(
        topics=[("/parameter_events", ["rcl_interfaces/msg/ParameterEvent"])],
        publishers_by_topic={"/parameter_events": [_FakeEndpointInfo(qos_profile=_qos())]},
    )
    manager = SubscriptionManager(node, _config(), [], clock=_fixed_clock(0.0), **_FAST_SETTLE)
    manager.start()

    assert manager.reports() == []


def test_add_exclude_rule_previews_the_affected_count_without_unsubscribing_yet() -> None:
    """`add_exclude_rule` must be callable off the spin-loop thread (the TUI's own, from the
    Options screen) -- so it only previews the count and defers any `destroy_subscription`
    to `exclusion.sweep_excluded_topics` (see `maintain()`), never touching the subscription itself."""
    node = _FakeNode(
        topics=[("/odom", ["nav_msgs/msg/Odometry"])],
        publishers_by_topic={"/odom": [_FakeEndpointInfo(qos_profile=_qos())]},
    )
    manager = SubscriptionManager(node, _config(), [], clock=_fixed_clock(0.0), **_FAST_SETTLE)
    manager.start()
    assert {sub.topic for sub in node.subscriptions} == {"/odom"}

    affected = manager.add_exclude_rule(ExcludeRule("/odom"))

    assert affected == 1
    assert {sub.topic for sub in node.subscriptions} == {"/odom"}  # not torn down yet
    assert manager.reports()[0].tier == "vitals"  # still reporting as vitals, not excluded yet


def test_maintain_sweeps_a_newly_matched_topic_into_excluded() -> None:
    """The actual move (and subscription teardown) happens on `maintain()` -- the spin-loop
    thread -- one cycle after `add_exclude_rule` added the rule."""
    node = _FakeNode(
        topics=[("/odom", ["nav_msgs/msg/Odometry"])],
        publishers_by_topic={"/odom": [_FakeEndpointInfo(qos_profile=_qos())]},
    )
    manager = SubscriptionManager(node, _config(), [], clock=_fixed_clock(0.0), **_FAST_SETTLE)
    manager.start()
    manager.add_exclude_rule(ExcludeRule("/odom"))

    manager.maintain()

    assert node.subscriptions == []  # the live subscription was torn down
    reports = manager.reports()
    assert len(reports) == 1
    assert reports[0].topic == "/odom"
    assert reports[0].tier == "excluded"


def test_add_exclude_rule_is_a_no_op_for_an_already_effective_rule() -> None:
    config = TestudoConfig(exclude_topics=[ExcludeRule("/odom")])
    manager = SubscriptionManager(_FakeNode(topics=[], publishers_by_topic={}), config, [], clock=_fixed_clock(0.0))
    assert manager.add_exclude_rule(ExcludeRule("/odom")) is None
    assert manager.exclude_rules() == [ExcludeRule("/odom")]


def test_remove_exclude_rule_lets_a_topic_get_rediscovered() -> None:
    node = _FakeNode(
        topics=[("/odom", ["nav_msgs/msg/Odometry"])],
        publishers_by_topic={"/odom": [_FakeEndpointInfo(qos_profile=_qos())]},
    )
    config = TestudoConfig(exclude_topics=[ExcludeRule("/odom")])
    manager = SubscriptionManager(node, config, [], clock=_fixed_clock(0.0), **_FAST_SETTLE)
    manager.start()
    assert manager.reports()[0].tier == "excluded"

    removed = manager.remove_exclude_rule(ExcludeRule("/odom"))
    assert removed is True
    assert manager.remove_exclude_rule(ExcludeRule("/odom")) is False  # already gone

    from testudo.core.maintenance import discover_and_subscribe

    discover_and_subscribe(manager)
    assert {sub.topic for sub in node.subscriptions} == {"/odom"}
    assert manager.reports()[0].tier == "vitals"


def test_remove_then_immediately_readd_the_same_rule_does_not_leave_the_topic_invisible() -> None:
    """Regression: `remove_exclude_rule` evicts a topic's bookkeeping so periodic rediscovery
    (throttled to `rediscovery_interval_seconds`, 5s by default) can pick it back up -- but a
    remove immediately followed by re-adding the same rule (exactly what happens clicking
    delete then re-typing the same pattern in the Options screen) used to leave the topic with
    no report at all -- neither excluded nor vitals -- until that whole interval elapsed,
    because `maintain()`'s rediscovery pass was still throttled and had nothing to rediscover."""
    node = _FakeNode(
        topics=[("/odom", ["nav_msgs/msg/Odometry"])],
        publishers_by_topic={"/odom": [_FakeEndpointInfo(qos_profile=_qos())]},
    )
    manager = SubscriptionManager(node, _config(), [], clock=_fixed_clock(0.0), **_FAST_SETTLE)
    manager.start()
    manager.add_exclude_rule(ExcludeRule("/odom"))
    manager.maintain()
    assert manager.reports()[0].tier == "excluded"

    manager.remove_exclude_rule(ExcludeRule("/odom"))
    manager.add_exclude_rule(ExcludeRule("/odom"))
    manager.maintain()  # no wall-clock time passes between these calls in this test

    reports = manager.reports()
    assert len(reports) == 1
    assert reports[0].topic == "/odom"
    assert reports[0].tier == "excluded"


def test_full_tier_topic_with_no_messages_is_error_not_plugin_default() -> None:
    node = _FakeNode(
        topics=[("/beep", ["std_msgs/msg/Empty"])],
        publishers_by_topic={"/beep": [_FakeEndpointInfo(qos_profile=_qos())]},
    )
    discovered = [DiscoveredPlugin(name="empty", plugin_class=_EmptyPlugin, source="packaged")]
    manager = SubscriptionManager(node, _config(), discovered, clock=_fixed_clock(0.0), **_FAST_SETTLE)
    manager.start()

    reports = manager.reports()
    assert reports[0].status.severity == Severity.ERROR
    assert "no messages received" in reports[0].status.message


def test_full_tier_thresholds_merge_topic_config_over_plugin_defaults() -> None:
    node = _FakeNode(
        topics=[("/odom", ["nav_msgs/msg/Odometry"])],
        publishers_by_topic={"/odom": [_FakeEndpointInfo(qos_profile=_qos())]},
    )
    discovered = [DiscoveredPlugin(name="recording", plugin_class=_RecordingPlugin, source="packaged")]
    config = TestudoConfig(
        topics={
            "nav_msgs/msg/Odometry": [
                TopicConfig(
                    name="/odom",
                    msg_type="nav_msgs/msg/Odometry",
                    thresholds={"custom": ThresholdZone(green=42.0)},
                )
            ]
        }
    )
    manager = SubscriptionManager(node, config, discovered, clock=_fixed_clock(0.0), **_FAST_SETTLE)
    manager.start()

    plugin = list(manager._full_tier.values())[0].plugin
    assert plugin.thresholds["custom"] == ThresholdZone(green=42.0)


def test_related_topic_is_routed_to_same_plugin_instance_not_subscribed_separately() -> None:
    node = _FakeNode(
        topics=[
            ("/odom", ["nav_msgs/msg/Odometry"]),
            ("/cmd_vel", ["geometry_msgs/msg/Twist"]),
        ],
        publishers_by_topic={
            "/odom": [_FakeEndpointInfo(qos_profile=_qos())],
            "/cmd_vel": [_FakeEndpointInfo(qos_profile=_qos())],
        },
    )
    discovered = [DiscoveredPlugin(name="recording", plugin_class=_RecordingPlugin, source="packaged")]
    config = TestudoConfig(
        topics={
            "nav_msgs/msg/Odometry": [
                TopicConfig(
                    name="/odom",
                    msg_type="nav_msgs/msg/Odometry",
                    related_topics={"cmd_vel": "/cmd_vel"},
                )
            ]
        }
    )
    manager = SubscriptionManager(node, config, discovered, clock=_fixed_clock(0.0), **_FAST_SETTLE)
    manager.start()

    # Exactly two subscriptions: /odom (primary) and /cmd_vel, whose own
    # subscription also feeds the plugin reading it -- not a third companion.
    assert len(node.subscriptions) == 2
    subscribed_topics = {sub.topic for sub in node.subscriptions}
    assert subscribed_topics == {"/odom", "/cmd_vel"}

    plugin = list(manager._full_tier.values())[0].plugin
    cmd_vel_sub = next(sub for sub in node.subscriptions if sub.topic == "/cmd_vel")
    sentinel = object()
    cmd_vel_sub.callback(sentinel)
    assert plugin.calls == [("/cmd_vel", sentinel)]

    # Being another topic's input doesn't take away /cmd_vel's own monitoring.
    odom_sub = next(sub for sub in node.subscriptions if sub.topic == "/odom")
    odom_sub.callback(object())
    reports = manager.reports()
    assert [r.topic for r in reports] == ["/cmd_vel", "/odom"]


def _sharing_manager(declared: list[TopicConfig], topics: list[tuple[str, list[str]]]) -> tuple[_FakeNode, SubscriptionManager]:
    node = _FakeNode(
        topics=topics,
        publishers_by_topic={name: [_FakeEndpointInfo(qos_profile=_qos())] for name, _types in topics},
    )
    discovered = [DiscoveredPlugin(name="recording", plugin_class=_RecordingPlugin, source="packaged")]
    by_type: dict[str, list[TopicConfig]] = {}
    for topic_config in declared:
        by_type.setdefault(topic_config.msg_type, []).append(topic_config)
    manager = SubscriptionManager(node, TestudoConfig(topics=by_type), discovered, clock=_fixed_clock(0.0), **_FAST_SETTLE)
    manager.start()
    return node, manager


def _odom_config(name: str, related: dict[str, str] | None = None) -> TopicConfig:
    return TopicConfig(name=name, msg_type="nav_msgs/msg/Odometry", related_topics=related or {})


def test_two_topics_sharing_a_related_topic_both_get_its_messages() -> None:
    node, manager = _sharing_manager(
        [_odom_config("/odom_a", {"cmd_vel": "/cmd_vel"}), _odom_config("/odom_b", {"cmd_vel": "/cmd_vel"})],
        [("/odom_a", ["nav_msgs/msg/Odometry"]), ("/odom_b", ["nav_msgs/msg/Odometry"]), ("/cmd_vel", ["geometry_msgs/msg/Twist"])],
    )
    companions = [sub for sub in node.subscriptions if sub.topic == "/cmd_vel"]
    assert len(companions) == 1
    sentinel = object()
    companions[0].callback(sentinel)
    for name in ("/odom_a", "/odom_b"):
        assert manager._full_tier[name].plugin.calls == [("/cmd_vel", sentinel)]


def test_a_declared_topic_stays_monitored_when_another_topic_also_reads_it() -> None:
    node, manager = _sharing_manager(
        [_odom_config("/odom_a", {"odom": "/odom_b"}), _odom_config("/odom_b")],
        [("/odom_a", ["nav_msgs/msg/Odometry"]), ("/odom_b", ["nav_msgs/msg/Odometry"])],
    )
    subscriptions = [sub for sub in node.subscriptions if sub.topic == "/odom_b"]
    assert len(subscriptions) == 1
    sentinel = object()
    subscriptions[0].callback(sentinel)
    assert manager._full_tier["/odom_a"].plugin.calls == [("/odom_b", sentinel)]
    assert manager._full_tier["/odom_b"].plugin.calls == [("/odom_b", sentinel)]
    assert [report.topic for report in manager.reports()] == ["/odom_a", "/odom_b"]


def test_a_declared_topic_no_plugin_checks_still_feeds_its_listeners() -> None:
    node, manager = _sharing_manager(
        [_odom_config("/odom_a", {"cmd_vel": "/cmd_vel"}), TopicConfig(name="/cmd_vel", msg_type="geometry_msgs/msg/Twist")],
        [("/odom_a", ["nav_msgs/msg/Odometry"]), ("/cmd_vel", ["geometry_msgs/msg/Twist"])],
    )
    subscriptions = [sub for sub in node.subscriptions if sub.topic == "/cmd_vel"]
    assert [sub.raw for sub in subscriptions] == [False]
    sentinel = object()
    subscriptions[0].callback(sentinel)
    assert manager._full_tier["/odom_a"].plugin.calls == [("/cmd_vel", sentinel)]
    assert manager._vitals["/cmd_vel"].message_count == 1


def test_a_declared_action_status_stays_monitored_when_a_topic_also_reads_it() -> None:
    status_topic = "/navigate_to_pose/_action/status"
    topics = [("/odom_a", ["nav_msgs/msg/Odometry"]), (status_topic, ["action_msgs/msg/GoalStatusArray"])]
    node = _FakeNode(topics, {name: [_FakeEndpointInfo(qos_profile=_qos())] for name, _types in topics})
    discovered = [DiscoveredPlugin(name="recording", plugin_class=_RecordingPlugin, source="packaged")]
    config = TestudoConfig(
        topics={"nav_msgs/msg/Odometry": [_odom_config("/odom_a", {"goal_status": status_topic})]},
        actions=[ActionConfig(name="navigate_to_pose")],
    )
    manager = SubscriptionManager(node, config, discovered, clock=_fixed_clock(0.0), **_FAST_SETTLE)
    manager.start()
    subscriptions = [sub for sub in node.subscriptions if sub.topic == status_topic]
    assert len(subscriptions) == 1
    assert status_topic in {report.topic for report in manager.reports()}
    sentinel = object()
    subscriptions[0].callback(sentinel)
    assert manager._full_tier["/odom_a"].plugin.calls == [(status_topic, sentinel)]


class _CompanionPlugin(_RecordingPlugin):
    """Names a companion topic by convention, the way a costmap does."""

    @classmethod
    def default_related_topics(cls, topic_name: str) -> dict[str, str]:
        return {"updates": f"{topic_name}_updates"}


def _companion_manager(topics: list[tuple[str, list[str]]]) -> tuple[_FakeNode, SubscriptionManager]:
    node = _FakeNode(topics, {name: [_FakeEndpointInfo(qos_profile=_qos())] for name, _types in topics})
    discovered = [DiscoveredPlugin(name="companion", plugin_class=_CompanionPlugin, source="packaged")]
    manager = SubscriptionManager(node, TestudoConfig(), discovered, clock=_fixed_clock(0.0), **_FAST_SETTLE)
    manager.start()
    return node, manager


def test_a_conventional_companion_topic_feeds_the_plugin_when_it_exists() -> None:
    node, manager = _companion_manager(
        [("/odom", ["nav_msgs/msg/Odometry"]), ("/odom_updates", ["std_msgs/msg/Empty"])]
    )
    assert manager._full_tier["/odom"].plugin.related_topics == {"updates": "/odom_updates"}
    sentinel = object()
    next(sub for sub in node.subscriptions if sub.topic == "/odom_updates" and not sub.raw).callback(sentinel)
    assert manager._full_tier["/odom"].plugin.calls == [("/odom_updates", sentinel)]


def test_a_conventional_companion_topic_that_does_not_exist_is_left_out() -> None:
    _node, manager = _companion_manager([("/odom", ["nav_msgs/msg/Odometry"])])
    assert manager._full_tier["/odom"].plugin.related_topics == {}


def _opted_out_manager(**config) -> tuple[_FakeNode, SubscriptionManager]:
    topics = [("/odom", ["nav_msgs/msg/Odometry"]), ("/odom_declared", ["nav_msgs/msg/Odometry"])]
    node = _FakeNode(topics, {name: [_FakeEndpointInfo(qos_profile=_qos())] for name, _types in topics})
    discovered = [DiscoveredPlugin(name="recording", plugin_class=_RecordingPlugin, source="packaged")]
    declared = {"nav_msgs/msg/Odometry": [_odom_config("/odom_declared")]}
    manager = SubscriptionManager(node, TestudoConfig(topics=declared, **config), discovered, clock=_fixed_clock(0.0), **_FAST_SETTLE)
    manager.start()
    return node, manager


def test_undeclared_topics_a_plugin_covers_are_checked_by_default() -> None:
    _node, manager = _opted_out_manager()
    assert set(manager._full_tier) == {"/odom", "/odom_declared"}


def test_opting_out_leaves_undeclared_topics_liveness_only() -> None:
    node, manager = _opted_out_manager(check_undeclared_topics=False)
    assert set(manager._full_tier) == {"/odom_declared"}
    assert set(manager._vitals) == {"/odom"}
    assert next(sub for sub in node.subscriptions if sub.topic == "/odom").raw


def _tf_only_manager(**config) -> SubscriptionManager:
    topics = [("/tf", ["tf2_msgs/msg/TFMessage"])]
    node = _FakeNode(topics, {"/tf": [_FakeEndpointInfo(qos_profile=_qos())]})

    class _TfPlugin(_RecordingPlugin):
        @classmethod
        def msg_types(cls) -> tuple[str, ...]:
            return ("tf2_msgs/msg/TFMessage",)

    discovered = [DiscoveredPlugin(name="tf", plugin_class=_TfPlugin, source="packaged")]
    manager = SubscriptionManager(node, TestudoConfig(**config), discovered, clock=_fixed_clock(0.0), **_FAST_SETTLE)
    manager.start()
    return manager


def test_opting_out_covers_tf_unless_frame_pairs_are_declared() -> None:
    assert set(_tf_only_manager(check_undeclared_topics=False)._vitals) == {"/tf"}
    pairs = [TFPairConfig(parent="map", child="odom")]
    assert set(_tf_only_manager(check_undeclared_topics=False, tf=pairs)._full_tier) == {"/tf"}


def _commanded_manager(**topic_config) -> tuple[SubscriptionManager, list[float]]:
    topics = [("/cmd_vel", ["geometry_msgs/msg/Twist"])]
    node = _FakeNode(topics, {"/cmd_vel": [_FakeEndpointInfo(qos_profile=_qos())]})
    clock_seconds = [100.0]

    class _Clock:
        def now(self):
            class _T:
                nanoseconds = int(clock_seconds[0] * 1e9)

            return _T()

    declared = {"geometry_msgs/msg/Twist": [TopicConfig(name="/cmd_vel", msg_type="geometry_msgs/msg/Twist", **topic_config)]}
    clock = TestudoClock(_Clock(), sim_time_query=lambda: False)
    manager = SubscriptionManager(node, TestudoConfig(topics=declared), [], clock=clock, **_FAST_SETTLE)
    manager.start()
    manager._nav2_rates.expected = {"/cmd_vel": ExpectedRate(20.0, "controller_frequency")}
    callback = node.subscriptions[0].callback
    for _ in range(30):
        clock_seconds[0] += 0.2  # 5 Hz, a quarter of what the controller is configured for
        callback(b"")
    return manager, clock_seconds


def test_a_topic_slower_than_nav2_asks_for_is_flagged() -> None:
    manager, _clock = _commanded_manager()
    status = manager.reports()[0].status
    assert status.code_severities == {"LIVE-004": Severity.ERROR}
    assert status.values["expected_rate"] == "20 Hz (controller_frequency)"


def test_a_rate_in_the_config_wins_over_nav2() -> None:
    manager, _clock = _commanded_manager(levels={"rate_hz": LevelsOverride(warn=1.0)})
    status = manager.reports()[0].status
    assert status.codes == {}
    assert "expected_rate" not in status.values


def test_decimation_skips_plugin_calls_faster_than_max_check_rate() -> None:
    node = _FakeNode(
        topics=[("/odom", ["nav_msgs/msg/Odometry"])],
        publishers_by_topic={"/odom": [_FakeEndpointInfo(qos_profile=_qos())]},
    )
    discovered = [DiscoveredPlugin(name="recording", plugin_class=_RecordingPlugin, source="packaged")]
    clock_seconds = [0.0]

    class _MutableClock:
        def now(self):
            class _T:
                nanoseconds = int(clock_seconds[0] * 1e9)

            return _T()

    testudo_clock = TestudoClock(_MutableClock(), sim_time_query=lambda: False)
    manager = SubscriptionManager(
        node, _config(), discovered, clock=testudo_clock, max_check_rate_hz=10.0, **_FAST_SETTLE
    )
    manager.start()

    sub = node.subscriptions[0]
    plugin = list(manager._full_tier.values())[0].plugin

    sub.callback("first")
    clock_seconds[0] = 0.02  # 20ms later, faster than the 100ms min interval -> decimated out
    sub.callback("second")
    clock_seconds[0] = 0.2  # past the min interval -> checked again
    sub.callback("third")

    assert [msg for _topic, msg in plugin.calls] == ["first", "third"]

    # Liveness still saw all three messages even though the plugin only saw two.
    vitals = list(manager._full_tier.values())[0].vitals
    assert vitals.message_count == 3


class _EveryMessagePlugin(_RecordingPlugin):
    """A plugin that opts out of decimation, counting how often its status is evaluated."""

    status_calls = 0

    @classmethod
    def needs_every_message(cls) -> bool:
        return True

    def get_status(self) -> CheckStatus:
        type(self).status_calls += 1
        return super().get_status()


def test_a_plugin_needing_every_message_sees_them_all_but_is_evaluated_at_the_decimated_rate() -> None:
    node = _FakeNode(
        topics=[("/odom", ["nav_msgs/msg/Odometry"])],
        publishers_by_topic={"/odom": [_FakeEndpointInfo(qos_profile=_qos())]},
    )
    discovered = [DiscoveredPlugin(name="every", plugin_class=_EveryMessagePlugin, source="packaged")]
    clock_seconds = [0.0]

    class _MutableClock:
        def now(self):
            class _T:
                nanoseconds = int(clock_seconds[0] * 1e9)

            return _T()

    manager = SubscriptionManager(
        node, _config(), discovered, clock=TestudoClock(_MutableClock(), sim_time_query=lambda: False),
        max_check_rate_hz=10.0, **_FAST_SETTLE
    )
    manager.start()
    sub = node.subscriptions[0]
    plugin = list(manager._full_tier.values())[0].plugin
    _EveryMessagePlugin.status_calls = 0

    for seconds, msg in ((0.0, "first"), (0.02, "second"), (0.04, "third"), (0.2, "fourth")):
        clock_seconds[0] = seconds
        sub.callback(msg)

    assert [msg for _topic, msg in plugin.calls] == ["first", "second", "third", "fourth"]
    assert _EveryMessagePlugin.status_calls == 2


def test_tf_watch_needs_every_message() -> None:
    from testudo.plugins.builtin.tf_watch import TFWatchPlugin

    assert TFWatchPlugin.needs_every_message()


def test_hysteresis_debounces_full_tier_severity_transitions() -> None:
    node = _FakeNode(
        topics=[("/odom", ["nav_msgs/msg/Odometry"])],
        publishers_by_topic={"/odom": [_FakeEndpointInfo(qos_profile=_qos())]},
    )
    discovered = [DiscoveredPlugin(name="recording", plugin_class=_RecordingPlugin, source="packaged")]
    clock_seconds = [0.0]

    class _MutableClock:
        def now(self):
            class _T:
                nanoseconds = int(clock_seconds[0] * 1e9)

            return _T()

    testudo_clock = TestudoClock(_MutableClock(), sim_time_query=lambda: False)
    manager = SubscriptionManager(
        node,
        _config(),
        discovered,
        clock=testudo_clock,
        max_check_rate_hz=1000.0,
        hysteresis_required_consecutive=3,
        **_FAST_SETTLE,
    )
    manager.start()

    sub = node.subscriptions[0]
    plugin = list(manager._full_tier.values())[0].plugin

    sub.callback("ok-1")
    assert manager.reports()[0].status.severity == Severity.OK

    # A single noisy ERROR sample shouldn't flip the debounced status yet.
    plugin.next_severity = Severity.ERROR
    clock_seconds[0] += 0.01
    sub.callback("error-1")
    assert manager.reports()[0].status.severity == Severity.OK

    clock_seconds[0] += 0.01
    sub.callback("error-2")
    assert manager.reports()[0].status.severity == Severity.OK

    # Third consecutive ERROR sample crosses the debounce threshold.
    clock_seconds[0] += 0.01
    sub.callback("error-3")
    assert manager.reports()[0].status.severity == Severity.ERROR


def _declared_scan_config() -> TestudoConfig:
    return TestudoConfig(
        publish=PublishConfig(topic="/diagnostics"),
        topics={"sensor_msgs/msg/LaserScan": [TopicConfig(name="/scan", msg_type="sensor_msgs/msg/LaserScan")]},
    )


def test_undeclared_topic_with_only_subscribers_is_an_idle_input_without_subscribing() -> None:
    node = _FakeNode(topics=[("/goal_pose", ["geometry_msgs/msg/PoseStamped"])], publishers_by_topic={})
    manager = SubscriptionManager(node, _config(), [], clock=_fixed_clock(0.0), **_FAST_SETTLE)
    manager.start()

    assert node.subscriptions == []
    reports = manager.reports()
    assert len(reports) == 1
    assert reports[0].status.severity == Severity.OK
    assert reports[0].status.label == "input"
    assert reports[0].status.values == {"subscribers": "/bt_navigator"}
    assert reports[0].status.codes == {}


def test_declared_topic_with_no_publishers_is_reported_as_error_without_subscribing() -> None:
    node = _FakeNode(topics=[("/scan", ["sensor_msgs/msg/LaserScan"])], publishers_by_topic={})
    manager = SubscriptionManager(node, _declared_scan_config(), [], clock=_fixed_clock(0.0), **_FAST_SETTLE)
    manager.start()

    assert node.subscriptions == []
    reports = manager.reports()
    assert len(reports) == 1
    assert reports[0].topic == "/scan"
    assert reports[0].status.severity == Severity.ERROR
    assert "no publishers" in reports[0].status.message
    assert "LIVE-001" in reports[0].status.codes


def test_rate_hz_is_checked_on_a_full_tier_topic_too() -> None:
    node = _FakeNode(
        topics=[("/odom", ["nav_msgs/msg/Odometry"])],
        publishers_by_topic={"/odom": [_FakeEndpointInfo(qos_profile=_qos())]},
    )
    discovered = [DiscoveredPlugin(name="recording", plugin_class=_RecordingPlugin, source="packaged")]
    rate = LevelsOverride(warn=9.0, error=5.0, given=frozenset({"warn", "error"}))
    config = TestudoConfig(
        topics={"nav_msgs/msg/Odometry": [TopicConfig(name="/odom", msg_type="nav_msgs/msg/Odometry", levels={"rate_hz": rate})]}
    )
    clock_seconds = [0.0]

    class _MutableClock:
        def now(self):
            class _T:
                nanoseconds = int(clock_seconds[0] * 1e9)

            return _T()

    clock = TestudoClock(_MutableClock(), sim_time_query=lambda: False)
    manager = SubscriptionManager(node, config, discovered, clock=clock, stale_after_seconds=100.0, **_FAST_SETTLE)
    manager.start()

    callback = node.subscriptions[0].callback
    callback(object())
    clock_seconds[0] = 0.5
    callback(object())  # 2 Hz: alive, plugin OK, but far under the 5 Hz error level
    report = manager.reports()[0]
    assert report.tier == "full"
    assert report.status.severity == Severity.ERROR
    assert report.status.code_severities == {"LIVE-004": Severity.ERROR}


def test_code_overrides_apply_to_reports_but_not_to_dead_stack_detection() -> None:
    node = _FakeNode(topics=[("/scan", ["sensor_msgs/msg/LaserScan"])], publishers_by_topic={})
    config = TestudoConfig(
        topics={
            "sensor_msgs/msg/LaserScan": [
                TopicConfig(name="/scan", msg_type="sensor_msgs/msg/LaserScan", codes={"LIVE-001": Severity.WARN})
            ]
        }
    )
    manager = SubscriptionManager(node, config, [], clock=_fixed_clock(0.0), **_FAST_SETTLE)
    manager.start()

    report = manager.reports()[0]
    assert report.status.severity == Severity.WARN
    assert report.status.code_severities == {"LIVE-001": Severity.WARN}
    assert build_reports(manager, code_overrides=False)[0].status.severity == Severity.ERROR


def test_a_topic_that_lost_its_publisher_is_an_error_not_an_idle_input() -> None:
    node = _FakeNode(topics=[("/odom", ["nav_msgs/msg/Odometry"])], publishers_by_topic={})
    manager = SubscriptionManager(node, _config(), [], clock=_fixed_clock(0.0), **_FAST_SETTLE)
    manager._had_publishers.add("/odom")  # seen with a publisher earlier, e.g. before a dead-stack clear
    manager.start()

    assert manager.reports()[0].status.severity == Severity.ERROR


def test_declared_vitals_topic_with_no_messages_received_is_error() -> None:
    node = _FakeNode(
        topics=[("/odom", ["nav_msgs/msg/Odometry"])],
        publishers_by_topic={"/odom": [_FakeEndpointInfo(qos_profile=_qos())]},
    )
    config = _declaring(("/odom", "nav_msgs/msg/Odometry"))
    manager = SubscriptionManager(node, config, [], clock=_fixed_clock(0.0), **_FAST_SETTLE)
    manager.start()

    reports = manager.reports()
    assert reports[0].status.severity == Severity.ERROR
    assert "no messages received" in reports[0].status.message


def test_undeclared_vitals_topic_that_never_published_is_idle() -> None:
    node = _FakeNode(
        topics=[("/evaluation", ["std_msgs/msg/String"])],
        publishers_by_topic={"/evaluation": [_FakeEndpointInfo(qos_profile=_qos())]},
    )
    manager = SubscriptionManager(node, _config(), [], clock=_fixed_clock(100.0), **_FAST_SETTLE)
    manager.start()

    status = manager.reports()[0].status
    assert (status.severity, status.label, status.codes) == (Severity.OK, "idle", {})


def test_undeclared_vitals_topic_that_stopped_publishing_still_goes_stale() -> None:
    node = _FakeNode(
        topics=[("/battery", ["sensor_msgs/msg/BatteryState"])],
        publishers_by_topic={"/battery": [_FakeEndpointInfo(qos_profile=_qos())]},
    )
    clock_seconds = [0.0]
    manager = SubscriptionManager(
        node, _config(), [], clock=_mutable_clock(clock_seconds), stale_after_seconds=2.0, **_FAST_SETTLE
    )
    manager.start()
    node.subscriptions[0].callback(b"")

    clock_seconds[0] = 10.0
    assert manager.reports()[0].status.severity == Severity.STALE


@pytest.mark.parametrize(
    "msg_type",
    [
        "visualization_msgs/msg/Marker",
        "visualization_msgs/msg/MarkerArray",
        "dwb_msgs/msg/LocalPlanEvaluation",
        "bond/msg/Status",
    ],
)
def test_on_demand_outputs_may_stop_publishing(msg_type: str) -> None:
    node = _FakeNode(
        topics=[("/output", [msg_type])],
        publishers_by_topic={"/output": [_FakeEndpointInfo(qos_profile=_qos())]},
    )
    clock_seconds = [0.0]
    manager = SubscriptionManager(
        node, _config(), [], clock=_mutable_clock(clock_seconds), stale_after_seconds=2.0, **_FAST_SETTLE
    )
    manager.start()
    node.subscriptions[0].callback(b"")

    clock_seconds[0] = 10.0
    status = manager.reports()[0].status
    assert (status.severity, status.label) == (Severity.OK, "idle")


def test_vitals_topic_recent_message_is_ok() -> None:
    node = _FakeNode(
        topics=[("/odom", ["nav_msgs/msg/Odometry"])],
        publishers_by_topic={"/odom": [_FakeEndpointInfo(qos_profile=_qos())]},
    )
    clock = _fixed_clock(0.0)
    manager = SubscriptionManager(node, _config(), [], clock=clock, stale_after_seconds=2.0, **_FAST_SETTLE)
    manager.start()

    # Simulate a message arriving by invoking the recorded raw-subscription callback.
    node.subscriptions[0].callback(b"")

    reports = manager.reports()
    assert reports[0].status.severity == Severity.OK
    assert reports[0].status.values["message_count"] == "1"


def test_vitals_topic_stale_after_threshold_elapses() -> None:
    node = _FakeNode(
        topics=[("/odom", ["nav_msgs/msg/Odometry"])],
        publishers_by_topic={"/odom": [_FakeEndpointInfo(qos_profile=_qos())]},
    )

    clock_seconds = [0.0]

    class _MutableClock:
        def now(self):
            class _T:
                nanoseconds = int(clock_seconds[0] * 1e9)

            return _T()

    testudo_clock = TestudoClock(_MutableClock(), sim_time_query=lambda: False)
    manager = SubscriptionManager(node, _config(), [], clock=testudo_clock, stale_after_seconds=2.0, **_FAST_SETTLE)
    manager.start()

    node.subscriptions[0].callback(b"")
    clock_seconds[0] = 5.0  # advance past the staleness threshold

    reports = manager.reports()
    assert reports[0].status.severity == Severity.STALE



def _manager_with_mutable_clock(node: "_FakeNode", config: TestudoConfig, clock_seconds: list[float]) -> SubscriptionManager:
    class _MutableClock:
        def now(self):
            class _T:
                nanoseconds = int(clock_seconds[0] * 1e9)

            return _T()

    testudo_clock = TestudoClock(_MutableClock(), sim_time_query=lambda: False)
    return SubscriptionManager(node, config, [], clock=testudo_clock, stale_after_seconds=2.0, **_FAST_SETTLE)


def test_transient_local_topic_reads_as_latched_not_stale() -> None:
    latched_qos = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
    node = _FakeNode(
        topics=[("/robot_description", ["std_msgs/msg/String"])],
        publishers_by_topic={"/robot_description": [_FakeEndpointInfo(qos_profile=latched_qos)]},
    )
    clock_seconds = [0.0]
    manager = _manager_with_mutable_clock(node, _config(), clock_seconds)
    manager.start()

    node.subscriptions[0].callback(b"")
    clock_seconds[0] = 60.0

    status = manager.reports()[0].status
    assert status.severity == Severity.OK
    assert status.label == "latched"


def test_topic_declared_event_driven_reads_as_idle_not_stale() -> None:
    node = _FakeNode(
        topics=[("/cmd_vel_nav", ["geometry_msgs/msg/Twist"])],
        publishers_by_topic={"/cmd_vel_nav": [_FakeEndpointInfo(qos_profile=_qos())]},
    )
    config = TestudoConfig(
        publish=PublishConfig(topic="/diagnostics"),
        topics={
            "geometry_msgs/msg/Twist": [
                TopicConfig(name="/cmd_vel_nav", msg_type="geometry_msgs/msg/Twist", event_driven=True)
            ]
        },
    )
    clock_seconds = [0.0]
    manager = _manager_with_mutable_clock(node, config, clock_seconds)
    manager.start()

    node.subscriptions[0].callback(b"")
    clock_seconds[0] = 60.0

    status = manager.reports()[0].status
    assert status.severity == Severity.OK
    assert status.label == "idle"

def test_excluded_topics_includes_default_set_and_configured_publish_topic() -> None:
    manager = SubscriptionManager(
        _FakeNode(topics=[], publishers_by_topic={}),
        _config(publish_topic="/my_diag"),
        [],
        clock=_fixed_clock(0.0),
        **_FAST_SETTLE,
    )
    excluded = manager.excluded_topics()
    assert "/parameter_events" in excluded
    assert "/rosout" in excluded
    assert "/my_diag" in excluded


def test_action_thresholds_merge_via_derived_status_topic() -> None:
    node = _FakeNode(
        topics=[("/navigate_to_pose/_action/status", ["action_msgs/msg/GoalStatusArray"])],
        publishers_by_topic={"/navigate_to_pose/_action/status": [_FakeEndpointInfo(qos_profile=_qos())]},
    )
    discovered = [DiscoveredPlugin(name="action", plugin_class=_ActionPlugin, source="packaged")]
    config = TestudoConfig(
        actions=[
            ActionConfig(
                name="navigate_to_pose",
                thresholds={"success_rate": ThresholdZone(green=0.5)},
            )
        ]
    )
    manager = SubscriptionManager(node, config, discovered, clock=_fixed_clock(0.0), **_FAST_SETTLE)
    manager.start()

    plugin = list(manager._full_tier.values())[0].plugin
    assert plugin.thresholds["success_rate"] == ThresholdZone(green=0.5)


def test_action_related_topic_is_routed_to_same_plugin_instance() -> None:
    node = _FakeNode(
        topics=[
            ("/navigate_to_pose/_action/status", ["action_msgs/msg/GoalStatusArray"]),
            ("/navigate_to_pose/_action/feedback", ["action_msgs/msg/GoalInfo"]),  # any installed type
        ],
        publishers_by_topic={
            "/navigate_to_pose/_action/status": [_FakeEndpointInfo(qos_profile=_qos())],
            "/navigate_to_pose/_action/feedback": [_FakeEndpointInfo(qos_profile=_qos())],
        },
    )
    discovered = [DiscoveredPlugin(name="action", plugin_class=_ActionPlugin, source="packaged")]
    config = TestudoConfig(
        actions=[
            ActionConfig(
                name="navigate_to_pose",
                related_topics={"feedback": "/navigate_to_pose/_action/feedback"},
            )
        ]
    )
    manager = SubscriptionManager(node, config, discovered, clock=_fixed_clock(0.0), **_FAST_SETTLE)
    manager.start()

    subscribed_topics = {sub.topic for sub in node.subscriptions}
    assert subscribed_topics == {"/navigate_to_pose/_action/status", "/navigate_to_pose/_action/feedback"}
    assert len(manager._full_tier) == 1  # one shared instance, not two

    plugin = list(manager._full_tier.values())[0].plugin
    feedback_sub = next(sub for sub in node.subscriptions if sub.topic == "/navigate_to_pose/_action/feedback")
    sentinel = object()
    feedback_sub.callback(sentinel)
    assert plugin.calls == [("/navigate_to_pose/_action/feedback", sentinel)]


def test_related_topic_of_an_uninstalled_type_is_one_warning_and_keeps_the_primary_topic() -> None:
    node = _FakeNode(
        topics=[
            ("/navigate_to_pose/_action/status", ["action_msgs/msg/GoalStatusArray"]),
            ("/navigate_to_pose/_action/feedback", ["nonexistent_testudo_pkg/action/Nope_FeedbackMessage"]),
        ],
        publishers_by_topic={
            "/navigate_to_pose/_action/status": [_FakeEndpointInfo(qos_profile=_qos())],
            "/navigate_to_pose/_action/feedback": [_FakeEndpointInfo(qos_profile=_qos())],
        },
    )
    discovered = [DiscoveredPlugin(name="action", plugin_class=_ActionPlugin, source="packaged")]
    config = TestudoConfig(
        actions=[
            ActionConfig(
                name="navigate_to_pose",
                related_topics={"feedback": "/navigate_to_pose/_action/feedback"},
            )
        ]
    )
    manager = SubscriptionManager(node, config, discovered, clock=_fixed_clock(0.0), **_FAST_SETTLE)

    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = records.append  # type: ignore[method-assign]
    tier_logger = logging.getLogger("testudo.core.tier_subscription")
    tier_logger.addHandler(handler)
    try:
        manager.start()
    finally:
        tier_logger.removeHandler(handler)

    assert {sub.topic for sub in node.subscriptions} == {"/navigate_to_pose/_action/status"}
    assert "/navigate_to_pose/_action/status" in manager._full_tier
    assert records and all(record.levelname == "WARNING" and not record.exc_info for record in records)


def _uninstalled_type_manager(**config_overrides: Any) -> tuple[_FakeNode, SubscriptionManager]:
    missing = "nonexistent_testudo_pkg/msg/Nope"
    node = _FakeNode(
        topics=[("/declared", [missing]), ("/undeclared", [missing])],
        publishers_by_topic={
            "/declared": [_FakeEndpointInfo(qos_profile=_qos())],
            "/undeclared": [_FakeEndpointInfo(qos_profile=_qos())],
        },
    )
    config = TestudoConfig(topics={missing: [TopicConfig(name="/declared", msg_type=missing)]}, **config_overrides)
    manager = SubscriptionManager(node, config, [], clock=_fixed_clock(0.0), **_FAST_SETTLE)
    manager.start()
    return node, manager


def test_declared_topic_of_an_uninstalled_type_is_an_error_and_an_undeclared_one_stays_out() -> None:
    node, manager = _uninstalled_type_manager()

    reports = {report.topic: report for report in manager.reports()}
    assert set(reports) == {"/declared"}
    assert reports["/declared"].status.severity == Severity.ERROR
    assert set(reports["/declared"].status.codes) == {"LIVE-005"}
    assert "nonexistent_testudo_pkg" in reports["/declared"].status.message
    assert node.subscriptions == []


def test_uninstalled_type_is_not_a_dead_topic_for_dead_stack_detection() -> None:
    _node, manager = _uninstalled_type_manager()
    assert manager.reports()[0].status.label != "liveness"


def test_excluding_a_declared_topic_of_an_uninstalled_type_clears_its_error() -> None:
    _node, manager = _uninstalled_type_manager(exclude_topics=[])
    manager.add_exclude_rule(ExcludeRule("/declared"))
    manager.maintain()

    reports = {report.topic: report for report in manager.reports()}
    assert reports["/declared"].tier == "excluded"
    assert reports["/declared"].status.severity == Severity.OK


class _SettableTime:
    """A clock a test moves by hand."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds

    def now(self) -> Any:
        return type("_Now", (), {"nanoseconds": int(self.seconds * 1e9)})()


def _sparse_pose_manager() -> tuple[_FakeNode, SubscriptionManager, _SettableTime]:
    from testudo.plugins.builtin.localization import LocalizationPlugin

    pose_type = "geometry_msgs/msg/PoseWithCovarianceStamped"
    node = _FakeNode(
        topics=[("/amcl_pose", [pose_type])], publishers_by_topic={"/amcl_pose": [_FakeEndpointInfo(qos_profile=_qos())]}
    )
    time_source = _SettableTime(1000.0)
    clock = TestudoClock(time_source, sim_time_query=lambda: False)
    discovered = [DiscoveredPlugin(name="localization", plugin_class=LocalizationPlugin, source="packaged")]
    manager = SubscriptionManager(node, TestudoConfig(), discovered, clock=clock, **_FAST_SETTLE)
    manager.start()
    return node, manager, time_source


def _stamped_pose(seconds: float, x: float = 0.0) -> Any:
    from geometry_msgs.msg import PoseWithCovarianceStamped

    msg = PoseWithCovarianceStamped()
    msg.header.frame_id = "map"
    msg.header.stamp.sec = int(seconds)
    msg.pose.pose.position.x = x
    msg.pose.pose.orientation.w = 1.0
    return msg


def test_a_silent_pose_estimate_reads_idle_not_stale_or_missing() -> None:
    node, manager, time_source = _sparse_pose_manager()
    assert manager.reports()[0].status.severity == Severity.OK  # no message yet: AMCL waits for motion

    node.subscriptions[0].callback(_stamped_pose(1000.0))
    time_source.seconds = 1600.0
    manager.tick()

    report = manager.reports()[0]
    assert report.status.severity == Severity.OK
    assert "idle_for_s" in report.status.values


def test_a_single_sparse_jump_is_raised_and_cleared_by_ticks_alone() -> None:
    node, manager, time_source = _sparse_pose_manager()
    node.subscriptions[0].callback(_stamped_pose(1000.0))
    time_source.seconds = 1002.0
    node.subscriptions[0].callback(_stamped_pose(1002.0, x=30.0))  # no further message follows

    for offset in (0.5, 1.0, 1.5):
        time_source.seconds = 1002.0 + offset
        manager.tick()
    assert "LOC-007" in manager.reports()[0].status.codes

    for offset in (11.0, 11.5, 12.0):
        time_source.seconds = 1002.0 + offset
        manager.tick()
    assert "LOC-007" not in manager.reports()[0].status.codes


def test_an_old_stamp_on_a_sparse_pose_is_not_flagged() -> None:
    node, manager, _time_source = _sparse_pose_manager()
    node.subscriptions[0].callback(_stamped_pose(0.0))  # an /initialpose sent by hand: stamp left at zero
    assert not any(code.startswith("STAMP-") for code in manager.reports()[0].status.codes)


def _map_odom_message(x: float) -> Any:
    from geometry_msgs.msg import TransformStamped
    from tf2_msgs.msg import TFMessage

    transform = TransformStamped()
    transform.header.frame_id = "map"
    transform.child_frame_id = "odom"
    transform.transform.translation.x = x
    transform.transform.rotation.w = 1.0
    return TFMessage(transforms=[transform])


def _tf_manager(**config: Any) -> tuple[_FakeNode, SubscriptionManager]:
    from testudo.plugins.builtin.tf_watch import TFWatchPlugin

    node = _FakeNode(
        topics=[("/tf", ["tf2_msgs/msg/TFMessage"])], publishers_by_topic={"/tf": [_FakeEndpointInfo(qos_profile=_qos())]}
    )
    discovered = [DiscoveredPlugin(name="tf_watch", plugin_class=TFWatchPlugin, source="packaged")]
    manager = SubscriptionManager(node, TestudoConfig(**config), discovered, clock=_fixed_clock(1000.0), **_FAST_SETTLE)
    manager.start()
    return node, manager


def test_map_odom_jump_is_reported_as_its_own_row() -> None:
    node, manager = _tf_manager()
    node.subscriptions[0].callback(_map_odom_message(0.0))
    node.subscriptions[0].callback(_map_odom_message(1.0))

    row = next(report for report in manager.reports() if report.topic == "map → odom")
    assert row.tier == "derived"
    assert row.plugin_group == "localization"
    assert "LOC-009" in row.status.codes


def test_a_derived_row_does_not_keep_a_dead_stack_looking_alive() -> None:
    from testudo.core.maintenance import _mostly_dead

    _node, manager = _tf_manager()
    dead = TopicReport("/tf", "tf2_msgs/msg/TFMessage", "full", CheckStatus(Severity.ERROR, "liveness", "no messages"))
    row = TopicReport("map → odom", "geometry_msgs/msg/TransformStamped", "derived", CheckStatus(Severity.OK, "l", "m"))
    assert _mostly_dead(manager, [dead, row])


def test_map_odom_code_takes_the_overrides_of_the_tf_topic() -> None:
    tf_type = "tf2_msgs/msg/TFMessage"
    node, manager = _tf_manager(topics={tf_type: [TopicConfig(name="/tf", msg_type=tf_type, codes={"LOC-009": Severity.OK})]})
    node.subscriptions[0].callback(_map_odom_message(0.0))
    node.subscriptions[0].callback(_map_odom_message(1.0))

    row = next(report for report in manager.reports() if report.topic == "map → odom")
    assert row.status.codes == {}
    assert row.status.severity == Severity.OK


def test_tf_watch_routes_tf_and_tf_static_to_one_instance() -> None:
    node = _FakeNode(
        topics=[
            ("/tf", ["tf2_msgs/msg/TFMessage"]),
            ("/tf_static", ["tf2_msgs/msg/TFMessage"]),
        ],
        publishers_by_topic={
            "/tf": [_FakeEndpointInfo(qos_profile=_qos())],
            "/tf_static": [_FakeEndpointInfo(qos_profile=_qos())],
        },
    )
    discovered = [DiscoveredPlugin(name="tf", plugin_class=_TFPlugin, source="packaged")]
    config = TestudoConfig(tf=[TFPairConfig(parent="map", child="base_link")])
    manager = SubscriptionManager(node, config, discovered, clock=_fixed_clock(0.0), **_FAST_SETTLE)
    manager.start()

    subscribed_topics = {sub.topic for sub in node.subscriptions}
    assert subscribed_topics == {"/tf", "/tf_static"}
    assert len(manager._full_tier) == 1  # one shared instance, not two

    plugin = list(manager._full_tier.values())[0].plugin
    assert plugin.watched_pairs == [("map", "base_link")]

    tf_sub = next(sub for sub in node.subscriptions if sub.topic == "/tf")
    static_sub = next(sub for sub in node.subscriptions if sub.topic == "/tf_static")
    tf_sub.callback("dynamic-msg")
    static_sub.callback("static-msg")
    assert plugin.calls == [("/tf", "dynamic-msg"), ("/tf_static", "static-msg")]

    # /tf_static never gets its own report row.
    reports = manager.reports()
    assert [r.topic for r in reports] == ["/tf"]


def test_related_topic_message_calls_on_tick_even_before_primary_topic_does() -> None:
    """Regression: a latched (transient-local) related topic like /tf_static can deliver
    before the primary topic's first decimated check ever calls on_tick. If the related-topic
    callback didn't also call on_tick, a plugin timestamping via `now_seconds` would see its
    plugin-default (never-ticked) clock value for that message -- e.g. TFWatchPlugin recording
    an edge as received at t=0 while `now` later jumps to a real wall-clock value, making the
    edge look enormously stale forever.
    """
    node = _FakeNode(
        topics=[
            ("/tf", ["tf2_msgs/msg/TFMessage"]),
            ("/tf_static", ["tf2_msgs/msg/TFMessage"]),
        ],
        publishers_by_topic={
            "/tf": [_FakeEndpointInfo(qos_profile=_qos())],
            "/tf_static": [_FakeEndpointInfo(qos_profile=_qos())],
        },
    )
    discovered = [DiscoveredPlugin(name="tf", plugin_class=_TFPlugin, source="packaged")]
    manager = SubscriptionManager(node, _config(), discovered, clock=_fixed_clock(42.0), **_FAST_SETTLE)
    manager.start()

    static_sub = next(sub for sub in node.subscriptions if sub.topic == "/tf_static")
    # The related topic delivers first -- before /tf has ever been checked.
    static_sub.callback("static-msg")

    plugin = list(manager._full_tier.values())[0].plugin
    assert plugin.tick_values == [42.0]


def test_tf_watch_inactive_without_tf_topic_on_graph() -> None:
    node = _FakeNode(topics=[], publishers_by_topic={})
    discovered = [DiscoveredPlugin(name="tf", plugin_class=_TFPlugin, source="packaged")]
    manager = SubscriptionManager(node, _config(), discovered, clock=_fixed_clock(0.0), **_FAST_SETTLE)
    manager.start()

    assert node.subscriptions == []
    assert manager.reports() == []


class _FakeGoalState:
    def __init__(self, label: str) -> None:
        self.label = label


class _FakeTransitionEvent:
    def __init__(self, label: str) -> None:
        self.goal_state = _FakeGoalState(label)


def test_lifecycle_tracking_subscribes_transition_event_without_its_own_report_row() -> None:
    node = _FakeNode(
        topics=[
            ("/controller_server/transition_event", ["lifecycle_msgs/msg/TransitionEvent"]),
            ("/controller_server/some_topic", ["std_msgs/msg/Empty"]),
        ],
        publishers_by_topic={
            "/controller_server/transition_event": [
                _FakeEndpointInfo(qos_profile=_qos(), node_name="controller_server", node_namespace="/")
            ],
            "/controller_server/some_topic": [
                _FakeEndpointInfo(qos_profile=_qos(), node_name="controller_server", node_namespace="/")
            ],
        },
    )
    config = _declaring(("/controller_server/some_topic", "std_msgs/msg/Empty"))
    manager = SubscriptionManager(node, config, [], clock=_fixed_clock(0.0), **_FAST_SETTLE)
    manager.start()

    reports = manager.reports()
    assert [r.topic for r in reports] == ["/controller_server/some_topic"]
    assert reports[0].status.severity == Severity.ERROR  # no messages yet, not suppressed: node state unknown

    transition_sub = next(sub for sub in node.subscriptions if sub.topic == "/controller_server/transition_event")
    transition_sub.callback(_FakeTransitionEvent("inactive"))

    suppressed_reports = manager.reports()
    assert suppressed_reports[0].status.severity == Severity.OK
    assert suppressed_reports[0].status.label == "suppressed"
    assert "inactive" in suppressed_reports[0].status.message


def test_lifecycle_state_is_seeded_via_get_state_for_a_node_already_inactive() -> None:
    """Regression: `~/transition_event` is VOLATILE, so a late-joining subscriber (Testudo,
    always) never sees a transition that happened before it started observing. A node that
    was already inactive/unconfigured at startup must still be suppressed immediately, via
    a `~/get_state` service query -- not only after some future transition happens to occur
    during the (typically short) observation window.
    """
    node = _FakeNode(
        topics=[
            ("/controller_server/transition_event", ["lifecycle_msgs/msg/TransitionEvent"]),
            ("/controller_server/some_topic", ["std_msgs/msg/Empty"]),
        ],
        publishers_by_topic={
            "/controller_server/transition_event": [
                _FakeEndpointInfo(qos_profile=_qos(), node_name="controller_server", node_namespace="/")
            ],
            "/controller_server/some_topic": [
                _FakeEndpointInfo(qos_profile=_qos(), node_name="controller_server", node_namespace="/")
            ],
        },
        # The node was already inactive before Testudo ever subscribed to anything.
        get_state_responses={"/controller_server": "inactive"},
    )
    manager = SubscriptionManager(node, _config(), [], clock=_fixed_clock(0.0), **_FAST_SETTLE)
    manager.start()

    reports = manager.reports()
    assert reports[0].status.label == "suppressed"
    assert reports[0].status.severity == Severity.OK


def test_lifecycle_state_seeding_is_skipped_gracefully_when_service_unavailable() -> None:
    node = _FakeNode(
        topics=[
            ("/controller_server/transition_event", ["lifecycle_msgs/msg/TransitionEvent"]),
            ("/controller_server/some_topic", ["std_msgs/msg/Empty"]),
        ],
        publishers_by_topic={
            "/controller_server/transition_event": [
                _FakeEndpointInfo(qos_profile=_qos(), node_name="controller_server", node_namespace="/")
            ],
            "/controller_server/some_topic": [
                _FakeEndpointInfo(qos_profile=_qos(), node_name="controller_server", node_namespace="/")
            ],
        },
        # No entry for /controller_server -> service unavailable.
    )
    manager = SubscriptionManager(node, _config(), [], clock=_fixed_clock(0.0), **_FAST_SETTLE)
    manager.start()  # must not raise

    reports = manager.reports()
    assert reports[0].status.label != "suppressed"


def test_rate_hz_threshold_flags_slow_vitals_topic() -> None:
    node = _FakeNode(
        topics=[("/local_costmap/costmap", ["nav_msgs/msg/OccupancyGrid"])],
        publishers_by_topic={"/local_costmap/costmap": [_FakeEndpointInfo(qos_profile=_qos())]},
    )
    config = TestudoConfig(
        topics={
            "nav_msgs/msg/OccupancyGrid": [
                TopicConfig(
                    name="/local_costmap/costmap",
                    msg_type="nav_msgs/msg/OccupancyGrid",
                    levels={"rate_hz": LevelsOverride(warn=4.0, error=1.0, given=frozenset({"warn", "error"}))},
                )
            ]
        }
    )
    clock_seconds = [0.0]

    class _MutableClock:
        def now(self):
            class _T:
                nanoseconds = int(clock_seconds[0] * 1e9)

            return _T()

    testudo_clock = TestudoClock(_MutableClock(), sim_time_query=lambda: False)
    manager = SubscriptionManager(node, config, [], clock=testudo_clock, stale_after_seconds=100.0, **_FAST_SETTLE)
    manager.start()

    sub = node.subscriptions[0]
    sub.callback(b"")
    clock_seconds[0] = 5.0  # 1 message at t=0, "now" advances but no second sample -> rate is None yet
    reports = manager.reports()
    assert reports[0].status.severity == Severity.OK  # rate_hz() is None with <2 samples -> untested, defaults OK

    sub.callback(b"")  # second message at t=5.0 -> rate = 1/5 = 0.2 Hz, well below orange(1.0)
    reports = manager.reports()
    assert reports[0].status.severity == Severity.ERROR
    assert "below 1 Hz" in reports[0].status.message


def test_weight_for_topic_uses_declared_topic_weight() -> None:
    node = _FakeNode(
        topics=[("/odom", ["nav_msgs/msg/Odometry"])],
        publishers_by_topic={"/odom": [_FakeEndpointInfo(qos_profile=_qos())]},
    )
    config = TestudoConfig(
        topics={"nav_msgs/msg/Odometry": [TopicConfig(name="/odom", msg_type="nav_msgs/msg/Odometry", weight=5.0)]}
    )
    manager = SubscriptionManager(node, config, [], clock=_fixed_clock(0.0), **_FAST_SETTLE)
    assert manager._weight_for_topic("/odom") == 5.0


def test_weight_for_topic_uses_declared_action_weight() -> None:
    node = _FakeNode(topics=[], publishers_by_topic={})
    config = TestudoConfig(
        actions=[ActionConfig(name="navigate_to_pose", weight=3.0)]
    )
    manager = SubscriptionManager(node, config, [], clock=_fixed_clock(0.0), **_FAST_SETTLE)
    assert manager._weight_for_topic("/navigate_to_pose/_action/status") == 3.0


def test_weight_for_topic_defaults_to_one() -> None:
    manager = SubscriptionManager(_FakeNode(topics=[], publishers_by_topic={}), _config(), [], clock=_fixed_clock(0.0), **_FAST_SETTLE)
    assert manager._weight_for_topic("/some/undeclared/topic") == 1.0


def test_overall_status_reflects_worst_report() -> None:
    node = _FakeNode(
        topics=[("/scan", ["sensor_msgs/msg/LaserScan"])],
        publishers_by_topic={},  # declared, no publishers -> ERROR report
    )
    manager = SubscriptionManager(node, _declared_scan_config(), [], clock=_fixed_clock(0.0), **_FAST_SETTLE)
    manager.start()
    overall = manager.overall_status(SeverityMode.WORST)
    assert overall.severity == Severity.ERROR
    assert overall.label == "overall"


def test_overall_status_weighted_mode_differs_from_worst() -> None:
    node = _FakeNode(
        topics=[("/odom", ["nav_msgs/msg/Odometry"]), ("/scan", ["sensor_msgs/msg/LaserScan"])],
        publishers_by_topic={
            "/odom": [_FakeEndpointInfo(qos_profile=_qos())],
            "/scan": [_FakeEndpointInfo(qos_profile=_qos())],
        },
    )
    config = _declaring(("/odom", "nav_msgs/msg/Odometry"), ("/scan", "sensor_msgs/msg/LaserScan"))
    manager = SubscriptionManager(node, config, [], clock=_fixed_clock(0.0), **_FAST_SETTLE)
    manager.start()
    # Both topics report ERROR (no messages received) -- worst == weighted here,
    # but confirm both modes at least run without differing unexpectedly.
    worst = manager.overall_status(SeverityMode.WORST)
    weighted = manager.overall_status(SeverityMode.WEIGHTED)
    assert worst.severity == Severity.ERROR
    assert weighted.severity == Severity.ERROR
    assert "weighted_score" in weighted.values
    assert "weighted_score" not in worst.values


def _mutable_clock(clock_seconds: list[float]) -> TestudoClock:
    class _MutableClock:
        def now(self):
            class _T:
                nanoseconds = int(clock_seconds[0] * 1e9)

            return _T()

    return TestudoClock(_MutableClock(), sim_time_query=lambda: False)


def test_maintain_is_a_noop_until_start_is_called() -> None:
    node = _FakeNode(
        topics=[("/odom", ["nav_msgs/msg/Odometry"])],
        publishers_by_topic={"/odom": [_FakeEndpointInfo(qos_profile=_qos())]},
    )
    manager = SubscriptionManager(
        node, _config(), [], clock=_fixed_clock(0.0), rediscovery_interval_seconds=0.0, **_FAST_SETTLE
    )

    # start() deliberately never called -- mirrors check/replay, which drive
    # tick() directly without it.
    manager.maintain()

    assert node.subscriptions == []
    assert manager.reports() == []


def test_maintain_subscribes_a_topic_added_to_the_graph_after_start() -> None:
    topics = [("/odom", ["nav_msgs/msg/Odometry"])]
    publishers: dict[str, list[object]] = {"/odom": [_FakeEndpointInfo(qos_profile=_qos())]}
    node = _FakeNode(topics=topics, publishers_by_topic=publishers)
    manager = SubscriptionManager(
        node, _config(), [], clock=_fixed_clock(0.0), rediscovery_interval_seconds=0.0, **_FAST_SETTLE
    )
    manager.start()
    assert {sub.topic for sub in node.subscriptions} == {"/odom"}

    # A new topic appears on the graph after Testudo started (e.g. the rest
    # of the stack was launched later).
    topics.append(("/scan", ["sensor_msgs/msg/LaserScan"]))
    publishers["/scan"] = [_FakeEndpointInfo(qos_profile=_qos())]

    manager.maintain()

    assert {sub.topic for sub in node.subscriptions} == {"/odom", "/scan"}
    assert {report.topic for report in manager.reports()} == {"/odom", "/scan"}


def test_maintain_retries_a_topic_that_previously_had_no_publishers() -> None:
    topics = [("/scan", ["sensor_msgs/msg/LaserScan"])]
    publishers: dict[str, list[object]] = {}
    node = _FakeNode(topics=topics, publishers_by_topic=publishers)
    manager = SubscriptionManager(
        node, _config(), [], clock=_fixed_clock(0.0), rediscovery_interval_seconds=0.0, **_FAST_SETTLE
    )
    manager.start()

    assert node.subscriptions == []
    reports = manager.reports()
    assert len(reports) == 1
    assert reports[0].status.label == "input"

    # The publisher shows up later.
    publishers["/scan"] = [_FakeEndpointInfo(qos_profile=_qos())]
    manager.maintain()

    assert {sub.topic for sub in node.subscriptions} == {"/scan"}
    reports = manager.reports()
    # Exactly one row -- the earlier "no publishers" entry must not linger
    # alongside the now-subscribed topic's own report.
    assert len(reports) == 1
    assert reports[0].status.label != "input"


def test_maintain_clears_all_topics_once_the_stack_goes_stale_and_disappears() -> None:
    topics = [("/odom", ["nav_msgs/msg/Odometry"]), ("/scan", ["sensor_msgs/msg/LaserScan"])]
    publishers: dict[str, list[object]] = {
        "/odom": [_FakeEndpointInfo(qos_profile=_qos())],
        "/scan": [_FakeEndpointInfo(qos_profile=_qos())],
    }
    node = _FakeNode(topics=topics, publishers_by_topic=publishers)
    clock_seconds = [0.0]
    manager = SubscriptionManager(
        node,
        _config(),
        [],
        clock=_mutable_clock(clock_seconds),
        stale_after_seconds=2.0,
        rediscovery_interval_seconds=0.0,
        stale_stack_fraction=0.9,
        stale_stack_grace_seconds=0.0,
        stale_check_interval_seconds=0.0,
        **_FAST_SETTLE,
    )
    manager.start()
    for sub in node.subscriptions:
        sub.callback(b"")
    assert len(node.subscriptions) == 2

    clock_seconds[0] = 10.0  # advance well past staleness for both topics
    assert all(report.status.severity == Severity.STALE for report in manager.reports())

    manager.maintain()  # first pass: mostly-stale detected, grace timer armed
    assert len(node.subscriptions) == 2  # not cleared yet -- grace not elapsed

    # The observed stack has actually gone away: nothing left on the graph.
    topics.clear()
    publishers.clear()

    manager.maintain()  # grace period (0s) has now elapsed -> clears
    assert node.subscriptions == []
    assert manager.reports() == []


def test_maintain_clears_when_some_topics_are_stale_and_others_never_received_a_message() -> None:
    """Regression: a topic with a publisher but zero messages ever received stays at
    ERROR ("no messages received") forever -- `liveness_status` checks
    `message_count == 0` before it ever looks at age, so that ERROR never
    transitions to STALE no matter how long nothing arrives. A real stack
    shutdown can easily leave some topics STALE and others stuck in this
    permanent zero-message ERROR state; both must count toward "the stack
    looks dead", or the fraction never reaches the threshold and stale rows
    accumulate forever.
    """
    topics = [("/odom", ["nav_msgs/msg/Odometry"]), ("/scan", ["sensor_msgs/msg/LaserScan"])]
    publishers: dict[str, list[object]] = {
        "/odom": [_FakeEndpointInfo(qos_profile=_qos())],
        "/scan": [_FakeEndpointInfo(qos_profile=_qos())],
    }
    node = _FakeNode(topics=topics, publishers_by_topic=publishers)
    clock_seconds = [0.0]
    manager = SubscriptionManager(
        node,
        _declaring(("/odom", "nav_msgs/msg/Odometry"), ("/scan", "sensor_msgs/msg/LaserScan")),
        [],
        clock=_mutable_clock(clock_seconds),
        stale_after_seconds=2.0,
        rediscovery_interval_seconds=0.0,
        stale_stack_fraction=0.9,
        stale_stack_grace_seconds=0.0,
        stale_check_interval_seconds=0.0,
        **_FAST_SETTLE,
    )
    manager.start()
    # /odom receives one message and later goes properly STALE; /scan never
    # receives anything and stays at "no messages received" ERROR.
    odom_sub = next(sub for sub in node.subscriptions if sub.topic == "/odom")
    odom_sub.callback(b"")

    clock_seconds[0] = 10.0
    reports = {report.topic: report.status.severity for report in manager.reports()}
    assert reports == {"/odom": Severity.STALE, "/scan": Severity.ERROR}

    manager.maintain()  # first pass: both count as dead, grace timer armed
    assert len(node.subscriptions) == 2  # not cleared yet -- grace not elapsed

    topics.clear()
    publishers.clear()

    manager.maintain()  # grace period (0s) has now elapsed -> clears
    assert node.subscriptions == []
    assert manager.reports() == []


def test_maintain_clears_a_dead_stack_whatever_its_never_published_topics() -> None:
    """An undeclared topic that never published is no evidence either way, so it can't hold the clear back."""
    topics = [("/odom", ["nav_msgs/msg/Odometry"]), ("/marker", ["std_msgs/msg/String"])]
    publishers: dict[str, list[object]] = {name: [_FakeEndpointInfo(qos_profile=_qos())] for name, _ in topics}
    node = _FakeNode(topics=topics, publishers_by_topic=publishers)
    clock_seconds = [0.0]
    manager = SubscriptionManager(
        node,
        _config(),
        [],
        clock=_mutable_clock(clock_seconds),
        stale_after_seconds=2.0,
        rediscovery_interval_seconds=0.0,
        stale_stack_fraction=0.9,
        stale_stack_grace_seconds=0.0,
        stale_check_interval_seconds=0.0,
        **_FAST_SETTLE,
    )
    manager.start()
    next(sub for sub in node.subscriptions if sub.topic == "/odom").callback(b"")

    clock_seconds[0] = 10.0
    manager.maintain()
    topics.clear()
    publishers.clear()
    manager.maintain()
    assert manager.reports() == []


def test_maintain_does_not_clear_for_a_content_check_error_on_an_actively_received_topic() -> None:
    """A plugin's own content-check ERROR (bad covariance, NaN, etc.) on a topic that's
    actively receiving messages must not count toward "the stack looks
    dead" -- only liveness failures (STALE, or the permanent zero-message/
    no-publishers ERROR) should. Distinguished via `status.label`:
    liveness-derived statuses are always labelled "liveness"
    (topic_report.py); a plugin's content status uses its own label.
    """
    topics = [("/odom", ["nav_msgs/msg/Odometry"]), ("/scan", ["sensor_msgs/msg/LaserScan"])]
    publishers: dict[str, list[object]] = {
        "/odom": [_FakeEndpointInfo(qos_profile=_qos())],
        "/scan": [_FakeEndpointInfo(qos_profile=_qos())],
    }
    node = _FakeNode(topics=topics, publishers_by_topic=publishers)
    clock_seconds = [0.0]
    discovered = [DiscoveredPlugin(name="recording", plugin_class=_RecordingPlugin, source="packaged")]
    manager = SubscriptionManager(
        node,
        _declaring(("/scan", "sensor_msgs/msg/LaserScan")),
        discovered,
        clock=_mutable_clock(clock_seconds),
        stale_after_seconds=2.0,
        rediscovery_interval_seconds=0.0,
        stale_stack_fraction=0.9,
        stale_stack_grace_seconds=0.0,
        stale_check_interval_seconds=0.0,
        hysteresis_required_consecutive=1,
        **_FAST_SETTLE,
    )
    manager.start()

    clock_seconds[0] = 10.0  # /odom's message arrives "now" -- not stale
    odom_sub = next(sub for sub in node.subscriptions if sub.topic == "/odom")
    manager._full_tier["/odom"].plugin.next_severity = Severity.ERROR
    odom_sub.callback(object())  # /odom: alive, but its plugin flags content ERROR

    # /scan never receives anything -- a genuine (if minority) liveness dead spot.
    reports = manager.reports()
    assert {r.topic: (r.status.label, r.status.severity) for r in reports} == {
        "/odom": ("recording", Severity.ERROR),
        "/scan": ("liveness", Severity.ERROR),
    }

    manager.maintain()
    manager.maintain()

    assert len(node.subscriptions) == 2


def test_per_plugin_publish_topics_and_node_profiling_topic_are_self_excluded() -> None:
    manager = SubscriptionManager(
        _FakeNode(topics=[], publishers_by_topic={}), TestudoConfig(), [], clock=_fixed_clock(0.0), **_FAST_SETTLE
    )
    assert manager.is_default_excluded("/testudo/localization")
    assert manager.is_default_excluded("/testudo/nodes")
    assert manager.is_default_excluded("/testudo/faults")
    assert not manager.is_default_excluded("/testudo_other")  # a namespace match is by whole segment
    combined = SubscriptionManager(
        _FakeNode(topics=[], publishers_by_topic={}),
        TestudoConfig(publish=PublishConfig(per_plugin=False)),
        [],
        clock=_fixed_clock(0.0),
        **_FAST_SETTLE,
    )
    assert not combined.is_default_excluded("/testudo/localization")
    assert combined.is_default_excluded("/diagnostics")
    assert combined.is_default_excluded(combined._config.faults.publish_topic)


def test_a_related_topic_found_first_still_reaches_its_listener_once() -> None:
    node = _FakeNode(
        topics=[("/cmd_vel", ["geometry_msgs/msg/Twist"]), ("/odom", ["nav_msgs/msg/Odometry"])],
        publishers_by_topic={
            "/cmd_vel": [_FakeEndpointInfo(qos_profile=_qos())],
            "/odom": [_FakeEndpointInfo(qos_profile=_qos())],
        },
    )
    discovered = [DiscoveredPlugin(name="recording", plugin_class=_RecordingPlugin, source="packaged")]
    odom = TopicConfig(name="/odom", msg_type="nav_msgs/msg/Odometry", related_topics={"cmd_vel": "/cmd_vel"})
    config = TestudoConfig(topics={"nav_msgs/msg/Odometry": [odom]})
    manager = SubscriptionManager(node, config, discovered, clock=_fixed_clock(0.0), **_FAST_SETTLE)
    manager.start()

    assert sorted(sub.topic for sub in node.subscriptions) == ["/cmd_vel", "/odom"]
    sentinel = object()
    next(sub for sub in node.subscriptions if sub.topic == "/cmd_vel").callback(sentinel)
    assert manager._full_tier["/odom"].plugin.calls == [("/cmd_vel", sentinel)]


class _RaisingPlugin(_EmptyPlugin):
    """A plugin that fails on every message."""

    def on_message(self, topic: str, msg: Any) -> None:
        raise ValueError("math domain error")


def _raising_manager(node: _FakeNode) -> SubscriptionManager:
    discovered = [DiscoveredPlugin(name="raising", plugin_class=_RaisingPlugin, source="packaged")]
    return SubscriptionManager(node, _config(), discovered, clock=_fixed_clock(0.0), **_FAST_SETTLE)


def _beep_node() -> _FakeNode:
    return _FakeNode(
        topics=[("/beep", ["std_msgs/msg/Empty"])],
        publishers_by_topic={"/beep": [_FakeEndpointInfo(qos_profile=_qos())]},
    )


def test_a_plugin_that_raises_is_reported_and_monitoring_goes_on(caplog: pytest.LogCaptureFixture) -> None:
    node = _beep_node()
    manager = _raising_manager(node)
    manager.start()

    node.subscriptions[0].callback(object())  # must not reach the executor, which would end the spin thread
    node.subscriptions[0].callback(object())

    status = manager.reports()[0].status
    assert status.severity == Severity.ERROR
    assert "math domain error" in status.codes["CHECK-001"]
    assert manager._full_tier["/beep"].vitals.message_count == 2


def test_resetting_stats_clears_a_check_failure() -> None:
    node = _beep_node()
    manager = _raising_manager(node)
    manager.start()
    node.subscriptions[0].callback(object())

    manager.reset_stats()
    assert "CHECK-001" not in manager.reports()[0].status.codes


def test_a_poll_never_runs_while_a_message_is_being_handled() -> None:
    node = _beep_node()
    discovered = [DiscoveredPlugin(name="empty", plugin_class=_EmptyPlugin, source="packaged")]
    manager = SubscriptionManager(node, _config(), discovered, clock=_fixed_clock(0.0), **_FAST_SETTLE)
    manager.start()
    held: list[bool] = []
    plugin = manager._full_tier["/beep"].plugin
    original = plugin.get_status
    plugin.get_status = lambda: (held.append(manager._lock._is_owned()), original())[1]

    manager.tick()
    manager.reports()
    node.subscriptions[0].callback(object())
    assert held and all(held)


def test_an_excluded_tf_stays_excluded_across_rediscovery() -> None:
    node = _FakeNode(
        topics=[("/tf", ["tf2_msgs/msg/TFMessage"]), ("/tf_static", ["tf2_msgs/msg/TFMessage"])],
        publishers_by_topic={
            "/tf": [_FakeEndpointInfo(qos_profile=_qos())],
            "/tf_static": [_FakeEndpointInfo(qos_profile=_qos())],
        },
    )
    discovered = [DiscoveredPlugin(name="tf", plugin_class=_TFPlugin, source="packaged")]
    config = TestudoConfig(exclude_topics=[ExcludeRule("/tf")])
    manager = SubscriptionManager(
        node, config, discovered, clock=_fixed_clock(0.0), rediscovery_interval_seconds=0.0, **_FAST_SETTLE
    )
    manager.start()
    manager.maintain()
    manager.maintain()

    assert node.subscriptions == []
    assert [(report.topic, report.tier) for report in manager.reports()] == [("/tf", "excluded")]


def test_resetting_stats_keeps_a_latched_topic_received() -> None:
    latched_qos = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
    node = _FakeNode(
        topics=[("/robot_description", ["std_msgs/msg/String"])],
        publishers_by_topic={"/robot_description": [_FakeEndpointInfo(qos_profile=latched_qos)]},
    )
    clock_seconds = [0.0]
    config = _declaring(("/robot_description", "std_msgs/msg/String"))
    manager = _manager_with_mutable_clock(node, config, clock_seconds)
    manager.start()
    node.subscriptions[0].callback(b"")

    manager.reset_stats()
    clock_seconds[0] = 60.0
    status = manager.reports()[0].status
    assert (status.severity, status.label) == (Severity.OK, "latched")


def test_quiet_rows_do_not_keep_a_closed_stack_looking_alive() -> None:
    quiet = [(f"/plan_{index}", ["nav_msgs/msg/Path"]) for index in range(3)]
    topics = [("/odom", ["nav_msgs/msg/Odometry"]), *quiet]
    publishers: dict[str, list[object]] = {name: [_FakeEndpointInfo(qos_profile=_qos())] for name, _ in topics}
    node = _FakeNode(topics=topics, publishers_by_topic=publishers)
    clock_seconds = [0.0]
    manager = SubscriptionManager(
        node,
        _config(),
        [],
        clock=_mutable_clock(clock_seconds),
        stale_after_seconds=2.0,
        rediscovery_interval_seconds=1000.0,
        stale_stack_fraction=0.9,
        stale_stack_grace_seconds=0.0,
        stale_check_interval_seconds=0.0,
        **_FAST_SETTLE,
    )
    manager.start()
    next(sub for sub in node.subscriptions if sub.topic == "/odom").callback(b"")

    clock_seconds[0] = 10.0
    manager.maintain()
    topics.clear()
    publishers.clear()
    manager.maintain()
    assert manager.reports() == []
