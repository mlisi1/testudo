"""Per-topic tier decision and subscription wiring: presence, vitals, full,
and a full-tier topic's related-topic companions.

Split out of subscription_manager.py (a companion module, like
maintenance.py and global_watches.py): "which tier does this topic get, and
how is that subscription wired" is the per-topic half of the manager's job,
separate from tracking state and building reports across all of them.
Every function here takes the `SubscriptionManager` instance directly and
reaches into its private state, the same way those modules do.

`process_full_tier_message` is shared with replay, so a live callback and a
replayed message get identical decimation, on_tick timing, and debouncing.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from rclpy.qos import DurabilityPolicy
from rosidl_runtime_py.utilities import get_message

from testudo.core.aggregator import HysteresisDebouncer
from testudo.core.discovery import (
    NoPublishersError,
    TopicInfo,
    describe_publisher,
    first_publisher_info,
    fully_qualified_node_name,
    resolve_subscription_qos,
    subscription_qos,
)
from testudo.core.exclusion import maybe_record_excluded
from testudo.core.stamp_check import StampCheck, stamp_check_for
from testudo.core.vitals import TopicVitals
from testudo.core.plugin_settings import build_plugin
from testudo.plugins.base import CheckPlugin

if TYPE_CHECKING:
    from testudo.core.subscription_manager import SubscriptionManager

_logger = logging.getLogger(__name__)

#: Always a companion of /tf's plugin, never a topic of its own, even if declared.
TF_STATIC_TOPIC = "/tf_static"


@dataclass
class FullTierState:
    """Per-topic bookkeeping for a full-tier subscription."""

    plugin: CheckPlugin
    vitals: TopicVitals
    debouncer: HysteresisDebouncer
    stamps: StampCheck = field(default_factory=StampCheck)
    last_checked_seconds: float | None = field(default=None)


def has_own_subscription(manager: SubscriptionManager, topic_name: str) -> bool:
    """True for a declared topic or action status: monitored in its own right, even when plugins also listen to it."""
    declared = topic_name in manager._topic_config_by_name or topic_name in manager._action_config_by_status_topic
    return declared and topic_name != TF_STATIC_TOPIC


def add_related_listener(manager: SubscriptionManager, related_topic_name: str, plugin: CheckPlugin, primary: str) -> None:
    """Make `plugin` (checking `primary`) one of the listeners of a related topic."""
    listeners = manager._related_topic_targets.setdefault(related_topic_name, [])
    # A primary subscribed again (un-excluded, say) replaces its previous plugin.
    listeners[:] = [listener for listener in listeners if listener[1] != primary]
    listeners.append((plugin, primary))


def feed_related_listeners(manager: SubscriptionManager, topic_name: str, msg: object, now: float) -> None:
    """Hand one message to every plugin using `topic_name` as a related topic.

    Not decimated. `on_tick` first, because a plugin timestamps with it: a
    latched topic like /tf_static can arrive before the primary's first check.
    """
    for plugin, _primary in manager._related_topic_targets.get(topic_name, ()):
        plugin.on_tick(now)
        plugin.on_message(topic_name, msg)


def _warn_unresolved(topic_name: str, msg_type_str: str) -> None:
    """Log a topic whose message type can't be imported here.

    Routine (a bag or robot using a package not installed here, such as
    nav2_msgs on a stack without Nav2): one line, not a traceback per topic;
    `-v` still shows the traceback.
    """
    _logger.warning(
        "could not resolve message class for '%s' (%s): its interface package isn't installed",
        topic_name,
        msg_type_str,
        exc_info=_logger.isEnabledFor(logging.DEBUG),
    )


def subscribe_topic(
    manager: SubscriptionManager, topic_name: str, msg_type_str: str, topics_by_name: dict[str, TopicInfo]
) -> None:
    """Resolve `topic_name`'s publishers and QoS, then subscribe it at the tier it qualifies for."""
    if maybe_record_excluded(manager, topic_name, msg_type_str):
        return
    manager._msg_type_by_topic[topic_name] = msg_type_str
    publisher_infos = manager._node.get_publishers_info_by_topic(topic_name)
    try:
        publisher_info = first_publisher_info(publisher_infos, topic_name)
    except NoPublishersError:
        _logger.info("topic '%s' has no publishers", topic_name)
        manager._no_publishers.add(topic_name)
        manager._subscribers_by_topic[topic_name] = tuple(
            fully_qualified_node_name(info.node_namespace, info.node_name)
            for info in manager._node.get_subscriptions_info_by_topic(topic_name)
        )
        return
    manager._no_publishers.discard(topic_name)
    manager._subscribers_by_topic.pop(topic_name, None)
    manager._had_publishers.add(topic_name)
    manager._publishers_by_topic[topic_name] = tuple(describe_publisher(info) for info in publisher_infos)
    manager._owning_node_by_topic[topic_name] = fully_qualified_node_name(
        publisher_info.node_namespace, publisher_info.node_name
    )
    qos = subscription_qos([info.qos_profile for info in publisher_infos])
    if qos.durability == DurabilityPolicy.TRANSIENT_LOCAL:
        manager._latched.add(topic_name)
    else:
        manager._latched.discard(topic_name)

    if msg_type_str in manager._presence_only_msg_types and topic_name not in manager._topic_config_by_name:
        manager._presence_only[topic_name] = msg_type_str
        return

    try:
        msg_class = get_message(msg_type_str)
    except Exception:
        _warn_unresolved(topic_name, msg_type_str)
        manager._unresolved[topic_name] = msg_type_str
        return

    plugin_class = manager._plugin_class_by_msg_type.get(msg_type_str)
    if plugin_class is None:
        subscribe_vitals_tier(manager, topic_name, msg_class, qos)
        return

    topic_config = manager._topic_config_by_name.get(topic_name)
    action_config = manager._action_config_by_status_topic.get(topic_name)
    related_topics: dict[str, str] = {}
    if topic_config is not None:
        related_topics = topic_config.related_topics
    if action_config is not None:
        related_topics = {**related_topics, **action_config.related_topics}
    conventional = plugin_class.default_related_topics(topic_name)
    related_topics = {**{key: name for key, name in conventional.items() if name in topics_by_name}, **related_topics}
    plugin = build_plugin(plugin_class, related_topics, topic_config, action_config)
    subscribe_full_tier(manager, topic_name, msg_class, qos, plugin, topics_by_name)


def subscribe_vitals_tier(manager: SubscriptionManager, topic_name: str, msg_class: type, qos_profile) -> None:
    """A raw (undeserialized) subscription that only records arrival times."""
    vitals = TopicVitals(topic=topic_name)
    manager._vitals[topic_name] = vitals

    if topic_name in manager._related_topic_names():
        # A plugin reads this topic's content, so it has to be deserialized after all.
        def _on_message(msg: object) -> None:
            now = manager._clock.now_seconds()
            vitals.record_arrival(now)
            feed_related_listeners(manager, topic_name, msg, now)

        manager._subscriptions[topic_name] = manager._node.create_subscription(
            msg_class, topic_name, _on_message, qos_profile
        )
        return

    def _on_raw_message(_raw_bytes: bytes) -> None:
        vitals.record_arrival(manager._clock.now_seconds())

    # raw=True: the middleware still needs the real message type to match
    # publishers, but the callback receives serialized bytes and never
    # deserializes -- that's the whole cost saving of this tier.
    manager._subscriptions[topic_name] = manager._node.create_subscription(
        msg_class, topic_name, _on_raw_message, qos_profile, raw=True
    )


def subscribe_full_tier(
    manager: SubscriptionManager,
    topic_name: str,
    msg_class: type,
    qos_profile,
    plugin: CheckPlugin,
    topics_by_name: dict[str, TopicInfo],
) -> FullTierState:
    """A typed subscription feeding `plugin` (freshly built), plus subscriptions to its related topics."""
    state = FullTierState(
        plugin=plugin,
        vitals=TopicVitals(topic=topic_name),
        debouncer=HysteresisDebouncer(manager._hysteresis_required_consecutive),
        stamps=stamp_check_for(manager._topic_config_by_name.get(topic_name)),
    )
    manager._full_tier[topic_name] = state

    def _on_message(msg: object) -> None:
        process_full_tier_message(manager, state, topic_name, msg, manager._clock.now_seconds())

    manager._subscriptions[topic_name] = manager._node.create_subscription(
        msg_class, topic_name, _on_message, qos_profile
    )

    for related_topic_name in plugin.related_topics.values():
        add_related_listener(manager, related_topic_name, plugin, topic_name)
        if not has_own_subscription(manager, related_topic_name):
            subscribe_related_topic(manager, related_topic_name, topics_by_name)

    return state


def process_full_tier_message(
    manager: SubscriptionManager, state: FullTierState, topic_name: str, msg: object, now: float
) -> None:
    """Vitals + decimated content-check for one full-tier message, at `now`.

    Shared by the live subscription callback and replay's
    `feed_replayed_message`, so both apply identical decimation,
    on_tick timing, and hysteresis debouncing.
    """
    state.vitals.record_arrival(now)
    feed_related_listeners(manager, topic_name, msg, now)
    max_check_rate_hz = manager._max_check_rate_hz
    min_check_interval = 1.0 / max_check_rate_hz if max_check_rate_hz > 0 else 0.0
    due = state.last_checked_seconds is None or (now - state.last_checked_seconds) >= min_check_interval
    if not due and not state.plugin.needs_every_message():
        return
    state.plugin.on_tick(now)
    state.plugin.on_message(topic_name, msg)
    if due:
        # Status (and hysteresis) at the decimated rate either way: a
        # plugin that needs every message still isn't re-evaluated per message.
        state.last_checked_seconds = now
        quiet = topic_name in manager._latched or state.plugin.silence_expected(manager._msg_type_by_topic.get(topic_name, ""))
        state.stamps.observe(msg, now, latched=quiet)
        state.debouncer.update(state.stamps.applied_to(state.plugin.get_status()))


def subscribe_related_topic(
    manager: SubscriptionManager, related_topic_name: str, topics_by_name: dict[str, TopicInfo]
) -> None:
    """One subscription feeding an undeclared related topic (e.g. cmd_vel) to all its listeners.

    Best-effort: if the related topic isn't currently on the graph or
    has no publishers, the cross-check it feeds simply never fires --
    this doesn't block or degrade the primary topic's own checks.
    """
    if related_topic_name in manager._related_subscriptions:
        return
    topic_info = topics_by_name.get(related_topic_name)
    if topic_info is None or not topic_info.msg_types:
        _logger.warning("related topic '%s' not found on the graph; skipping", related_topic_name)
        return
    try:
        qos_profile = resolve_subscription_qos(manager._node, related_topic_name)
    except NoPublishersError:
        _logger.warning("related topic '%s' has no publishers; skipping", related_topic_name)
        return
    try:
        msg_class = get_message(topic_info.msg_types[0])
    except Exception:
        _warn_unresolved(related_topic_name, topic_info.msg_types[0])
        return

    def _on_related_message(msg: object) -> None:
        feed_related_listeners(manager, related_topic_name, msg, manager._clock.now_seconds())

    manager._related_subscriptions[related_topic_name] = manager._node.create_subscription(
        msg_class, related_topic_name, _on_related_message, qos_profile
    )
