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
)
from testudo.core.exclusion import maybe_record_excluded
from testudo.core.vitals import TopicVitals
from testudo.plugins.base import CheckPlugin, ThresholdZone

if TYPE_CHECKING:
    from testudo.core.subscription_manager import SubscriptionManager

_logger = logging.getLogger(__name__)


@dataclass
class FullTierState:
    """Per-topic bookkeeping for a full-tier subscription."""

    plugin: CheckPlugin
    vitals: TopicVitals
    debouncer: HysteresisDebouncer
    last_checked_seconds: float | None = field(default=None)


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
        _logger.warning("topic '%s' has no publishers", topic_name)
        manager._no_publishers.add(topic_name)
        return
    manager._no_publishers.discard(topic_name)
    manager._publishers_by_topic[topic_name] = tuple(describe_publisher(info) for info in publisher_infos)
    manager._owning_node_by_topic[topic_name] = fully_qualified_node_name(
        publisher_info.node_namespace, publisher_info.node_name
    )
    if publisher_info.qos_profile.durability == DurabilityPolicy.TRANSIENT_LOCAL:
        manager._latched.add(topic_name)
    else:
        manager._latched.discard(topic_name)

    if msg_type_str in manager._presence_only_msg_types and topic_name not in manager._topic_config_by_name:
        manager._presence_only[topic_name] = msg_type_str
        return

    try:
        msg_class = get_message(msg_type_str)
    except Exception:
        _logger.exception("could not resolve message class for '%s' (%s)", topic_name, msg_type_str)
        return

    plugin_class = manager._plugin_class_by_msg_type.get(msg_type_str)
    if plugin_class is None:
        subscribe_vitals_tier(manager, topic_name, msg_class, publisher_info.qos_profile)
        return

    topic_config = manager._topic_config_by_name.get(topic_name)
    action_config = manager._action_config_by_status_topic.get(topic_name)
    thresholds = dict(plugin_class.default_thresholds())
    related_topics: dict[str, str] = {}
    if topic_config is not None:
        thresholds.update(topic_config.thresholds)
        related_topics = topic_config.related_topics
    if action_config is not None:
        thresholds.update(action_config.thresholds)
        related_topics = {**related_topics, **action_config.related_topics}
    subscribe_full_tier(
        manager, topic_name, msg_class, publisher_info.qos_profile, plugin_class, thresholds, related_topics, topics_by_name
    )


def subscribe_vitals_tier(manager: SubscriptionManager, topic_name: str, msg_class: type, qos_profile) -> None:
    """A raw (undeserialized) subscription that only records arrival times."""
    vitals = TopicVitals(topic=topic_name)
    manager._vitals[topic_name] = vitals

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
    plugin_class: type[CheckPlugin],
    thresholds: dict[str, ThresholdZone],
    related_topics: dict[str, str],
    topics_by_name: dict[str, TopicInfo],
) -> FullTierState:
    """A typed subscription feeding a fresh `plugin_class` instance, plus its related topics."""
    plugin = plugin_class(thresholds=thresholds, related_topics=related_topics)
    state = FullTierState(
        plugin=plugin,
        vitals=TopicVitals(topic=topic_name),
        debouncer=HysteresisDebouncer(manager._hysteresis_required_consecutive),
    )
    manager._full_tier[topic_name] = state

    def _on_message(msg: object) -> None:
        process_full_tier_message(manager, state, topic_name, msg, manager._clock.now_seconds())

    manager._subscriptions[topic_name] = manager._node.create_subscription(
        msg_class, topic_name, _on_message, qos_profile
    )

    for related_topic_name in related_topics.values():
        manager._related_topic_targets[related_topic_name] = (plugin, topic_name)
        subscribe_related_topic(manager, related_topic_name, plugin, topics_by_name)

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
    max_check_rate_hz = manager._max_check_rate_hz
    min_check_interval = 1.0 / max_check_rate_hz if max_check_rate_hz > 0 else 0.0
    if state.last_checked_seconds is not None and (now - state.last_checked_seconds) < min_check_interval:
        return
    state.last_checked_seconds = now
    state.plugin.on_tick(now)
    state.plugin.on_message(topic_name, msg)
    state.debouncer.update(state.plugin.get_status())


def subscribe_related_topic(
    manager: SubscriptionManager, related_topic_name: str, plugin: CheckPlugin, topics_by_name: dict[str, TopicInfo]
) -> None:
    """Feed a plugin's related-topic input (e.g. cmd_vel) to the same instance's `on_message`.

    Best-effort: if the related topic isn't currently on the graph or
    has no publishers, the cross-check it feeds simply never fires --
    this doesn't block or degrade the primary topic's own checks.
    """
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
        _logger.exception("could not resolve message class for related topic '%s'", related_topic_name)
        return

    def _on_related_message(msg: object) -> None:
        # Related-topic messages aren't decimated (no vitals tracking of
        # their own), but a plugin relying on on_tick's `now` (e.g.
        # TFWatchPlugin timestamping an edge) needs it kept current here
        # too -- a latched (transient-local) related topic like
        # /tf_static can otherwise deliver its message before the
        # primary topic's first check ever calls on_tick, leaving `now`
        # at its plugin-default and making that edge look stale forever.
        plugin.on_tick(manager._clock.now_seconds())
        plugin.on_message(related_topic_name, msg)

    manager._subscriptions[related_topic_name] = manager._node.create_subscription(
        msg_class, related_topic_name, _on_related_message, qos_profile
    )
