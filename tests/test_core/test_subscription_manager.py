"""Unit tests for SubscriptionManager's tier-assignment and reporting logic.

Uses a fake rclpy node (duck-typed: `get_topic_names_and_types`,
`get_publishers_info_by_topic`, `create_subscription`) so tier assignment,
exclusion, and report generation are all testable without a live ROS graph.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from rclpy.qos import QoSProfile

from testudo.core.clock import TestudoClock
from testudo.core.config import ActionConfig, PublishConfig, SeverityMode, TestudoConfig, TFPairConfig, TopicConfig
from testudo.core.subscription_manager import SubscriptionManager
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

    def get_publishers_info_by_topic(self, topic_name: str) -> list[object]:
        return self._publishers_by_topic.get(topic_name, [])

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
    """Test-only plugin covering action_msgs/msg/GoalStatusArray, mirroring Nav2GoalPlugin's shape."""

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

    # Exactly two subscriptions: /odom (primary) and /cmd_vel (related, fed
    # to the same plugin instance) -- not a third, independent vitals-tier
    # subscription to /cmd_vel from the generic topic loop.
    assert len(node.subscriptions) == 2
    subscribed_topics = {sub.topic for sub in node.subscriptions}
    assert subscribed_topics == {"/odom", "/cmd_vel"}

    plugin = list(manager._full_tier.values())[0].plugin
    cmd_vel_sub = next(sub for sub in node.subscriptions if sub.topic == "/cmd_vel")
    sentinel = object()
    cmd_vel_sub.callback(sentinel)
    assert plugin.calls == [("/cmd_vel", sentinel)]

    # /cmd_vel never gets its own report row -- it's an input, not a monitored topic.
    odom_sub = next(sub for sub in node.subscriptions if sub.topic == "/odom")
    odom_sub.callback(object())
    reports = manager.reports()
    assert [r.topic for r in reports] == ["/odom"]


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


def test_topic_with_no_publishers_is_reported_as_error_without_subscribing() -> None:
    node = _FakeNode(topics=[("/scan", ["sensor_msgs/msg/LaserScan"])], publishers_by_topic={})
    manager = SubscriptionManager(node, _config(), [], clock=_fixed_clock(0.0), **_FAST_SETTLE)
    manager.start()

    assert node.subscriptions == []
    reports = manager.reports()
    assert len(reports) == 1
    assert reports[0].topic == "/scan"
    assert reports[0].status.severity == Severity.ERROR
    assert "no publishers" in reports[0].status.message


def test_vitals_topic_with_no_messages_received_is_error() -> None:
    node = _FakeNode(
        topics=[("/odom", ["nav_msgs/msg/Odometry"])],
        publishers_by_topic={"/odom": [_FakeEndpointInfo(qos_profile=_qos())]},
    )
    manager = SubscriptionManager(node, _config(), [], clock=_fixed_clock(0.0), **_FAST_SETTLE)
    manager.start()

    reports = manager.reports()
    assert reports[0].status.severity == Severity.ERROR
    assert "no messages received" in reports[0].status.message


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
                action_type="nav2_msgs/action/NavigateToPose",
                thresholds={"success_rate": ThresholdZone(green=0.5)},
            )
        ]
    )
    manager = SubscriptionManager(node, config, discovered, clock=_fixed_clock(0.0), **_FAST_SETTLE)
    manager.start()

    plugin = list(manager._full_tier.values())[0].plugin
    assert plugin.thresholds["success_rate"] == ThresholdZone(green=0.5)


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
    manager = SubscriptionManager(node, _config(), [], clock=_fixed_clock(0.0), **_FAST_SETTLE)
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
                    thresholds={"rate_hz": ThresholdZone(green=4.0, orange=1.0)},
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
    assert "below configured threshold" in reports[0].status.message


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
        actions=[ActionConfig(name="navigate_to_pose", action_type="nav2_msgs/action/NavigateToPose", weight=3.0)]
    )
    manager = SubscriptionManager(node, config, [], clock=_fixed_clock(0.0), **_FAST_SETTLE)
    assert manager._weight_for_topic("/navigate_to_pose/_action/status") == 3.0


def test_weight_for_topic_defaults_to_one() -> None:
    manager = SubscriptionManager(_FakeNode(topics=[], publishers_by_topic={}), _config(), [], clock=_fixed_clock(0.0), **_FAST_SETTLE)
    assert manager._weight_for_topic("/some/undeclared/topic") == 1.0


def test_overall_status_reflects_worst_report() -> None:
    node = _FakeNode(
        topics=[("/scan", ["sensor_msgs/msg/LaserScan"])],
        publishers_by_topic={},  # no publishers -> ERROR report
    )
    manager = SubscriptionManager(node, _config(), [], clock=_fixed_clock(0.0), **_FAST_SETTLE)
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
    manager = SubscriptionManager(node, _config(), [], clock=_fixed_clock(0.0), **_FAST_SETTLE)
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
    assert "no publishers" in reports[0].status.message

    # The publisher shows up later.
    publishers["/scan"] = [_FakeEndpointInfo(qos_profile=_qos())]
    manager.maintain()

    assert {sub.topic for sub in node.subscriptions} == {"/scan"}
    reports = manager.reports()
    # Exactly one row -- the earlier "no publishers" entry must not linger
    # alongside the now-subscribed topic's own report.
    assert len(reports) == 1
    assert "no publishers" not in reports[0].status.message


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
        _config(),
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
