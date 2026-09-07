"""Subscription wiring for the two graph-wide (not per-declared-topic) watches.

Split out of subscription_manager.py because these two aren't instances of
the generic type-matched per-topic loop -- they're always-on, special-cased
side channels -- and grouping them here keeps that distinction visible
without bloating the main orchestrator file. Both functions take the
`SubscriptionManager` instance directly and reach into its private state;
they're companions to that class, not a separate public API.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from lifecycle_msgs.srv import GetState
from rosidl_runtime_py.utilities import get_message

from testudo.core.discovery import (
    LIFECYCLE_TRANSITION_EVENT_SUFFIX,
    NoPublishersError,
    TopicInfo,
    fully_qualified_node_name,
    list_lifecycle_nodes,
    resolve_publisher_info,
    resolve_subscription_qos,
)

if TYPE_CHECKING:
    from testudo.core.subscription_manager import SubscriptionManager

_logger = logging.getLogger(__name__)

TF_TYPE = "tf2_msgs/msg/TFMessage"
TF_TOPIC = "/tf"
TF_STATIC_TOPIC = "/tf_static"
TRANSITION_EVENT_TYPE = "lifecycle_msgs/msg/TransitionEvent"

#: How long to wait for a lifecycle node's `~/get_state` service, both to
#: appear and to respond, when seeding its current state at startup.
DEFAULT_GET_STATE_TIMEOUT_SECONDS = 0.5


def subscribe_tf_watch(manager: "SubscriptionManager", topics_by_name: dict[str, TopicInfo]) -> set[str]:
    """Subscribe /tf (+/tf_static) to one shared TF-watch plugin instance, if discovered and present.

    Handled separately from the generic per-topic loop because /tf and
    /tf_static share a message type: type-matching alone would give them
    two independent plugin instances instead of one that sees the whole
    tree. Reuses `_subscribe_full_tier`'s related-topics wiring to route
    /tf_static to the same instance as /tf.

    Returns the topic names claimed (subscribed or recorded as
    publisher-less) here, so `start()` can keep the generic per-topic loop
    from independently re-subscribing them -- /tf_static in particular
    isn't declared in `config.topics`, so the usual `related_topics`
    reservation never sees it.
    """
    plugin_class = manager._plugin_class_by_msg_type.get(TF_TYPE)
    if plugin_class is None or TF_TOPIC not in topics_by_name:
        return set()

    manager._msg_type_by_topic[TF_TOPIC] = TF_TYPE
    try:
        publisher_info = resolve_publisher_info(manager._node, TF_TOPIC)
    except NoPublishersError:
        _logger.warning("'%s' has no publishers", TF_TOPIC)
        manager._no_publishers.add(TF_TOPIC)
        return {TF_TOPIC}
    manager._owning_node_by_topic[TF_TOPIC] = fully_qualified_node_name(
        publisher_info.node_namespace, publisher_info.node_name
    )

    try:
        msg_class = get_message(TF_TYPE)
    except Exception:
        _logger.exception("could not resolve message class for '%s'", TF_TOPIC)
        return {TF_TOPIC}

    topic_config = manager._topic_config_by_name.get(TF_TOPIC)
    thresholds = dict(plugin_class.default_thresholds())
    if topic_config is not None:
        thresholds.update(topic_config.thresholds)
    related_topics = {"tf_static": TF_STATIC_TOPIC} if TF_STATIC_TOPIC in topics_by_name else {}

    state = manager._subscribe_full_tier(
        TF_TOPIC, msg_class, publisher_info.qos_profile, plugin_class, thresholds, related_topics, topics_by_name
    )

    # TF's watch list comes from config.tf (parent/child pairs), not the
    # generic thresholds/related_topics a plugin's __init__ takes -- an
    # optional, duck-typed hook, since it's specific to this one plugin.
    if hasattr(state.plugin, "set_watched_pairs"):
        state.plugin.set_watched_pairs([(pair.parent, pair.child) for pair in manager._config.tf])

    return {TF_TOPIC} | set(related_topics.values())


def subscribe_lifecycle_tracking(manager: "SubscriptionManager", topics_by_name: dict[str, TopicInfo]) -> None:
    """Subscribe every lifecycle node's `~/transition_event` topic to feed the lifecycle tracker.

    Also queries `~/get_state` once per node first: `~/transition_event` is
    VOLATILE (confirmed against a live rclpy_lifecycle node), so a
    late-joining subscriber -- which Testudo always is -- never sees
    transitions that already happened. Without this, a node that was
    already inactive/unconfigured before Testudo started observing would
    only get suppressed if it happened to transition again during the
    (typically short) observation window.

    Pure side-channel bookkeeping: these never get their own report row,
    only inform `suppress_if_inactive` for topics owned by that node.
    """
    for lifecycle_node in list_lifecycle_nodes(manager._node):
        status_topic = f"{lifecycle_node.node_name}{LIFECYCLE_TRANSITION_EVENT_SUFFIX}"
        if status_topic not in topics_by_name:
            continue

        _seed_current_lifecycle_state(manager, lifecycle_node.node_name)

        try:
            qos_profile = resolve_subscription_qos(manager._node, status_topic)
        except NoPublishersError:
            _logger.warning("lifecycle node '%s' has no transition_event publisher", lifecycle_node.node_name)
            continue
        try:
            msg_class = get_message(TRANSITION_EVENT_TYPE)
        except Exception:
            _logger.exception("could not resolve message class for '%s'", TRANSITION_EVENT_TYPE)
            return

        node_name = lifecycle_node.node_name

        def _on_transition_event(msg: object, _node_name: str = node_name) -> None:
            manager._lifecycle_tracker.on_transition_event(_node_name, msg.goal_state.label)

        manager._node.create_subscription(msg_class, status_topic, _on_transition_event, qos_profile)
        manager._msg_type_by_topic[status_topic] = TRANSITION_EVENT_TYPE


def _seed_current_lifecycle_state(manager: "SubscriptionManager", node_name: str) -> None:
    """Query `<node_name>/get_state` once and record the result, best-effort.

    A missing/slow service (node doesn't expose it, or doesn't respond
    within `get_state_timeout_seconds`) is logged and skipped -- the
    transition-event subscription still covers whatever happens from here.
    """
    client = manager._node.create_client(GetState, f"{node_name}/get_state")
    try:
        if not client.wait_for_service(timeout_sec=manager._get_state_timeout_seconds):
            _logger.warning("'%s/get_state' service not available; skipping initial state query", node_name)
            return
        future = client.call_async(GetState.Request())
        manager._spin_until_future_complete(manager._node, future, timeout_sec=manager._get_state_timeout_seconds)
        if not future.done() or future.result() is None:
            _logger.warning("'%s/get_state' did not respond in time", node_name)
            return
        manager._lifecycle_tracker.on_transition_event(node_name, future.result().current_state.label)
    finally:
        manager._node.destroy_client(client)
