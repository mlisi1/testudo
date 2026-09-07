"""Batch replay: feeds a rosbag2 recording through the same tier/threshold/
decimation/hysteresis pipeline a live check uses, then reports once at the end.

Topics are assigned to vitals/full tier exactly as `SubscriptionManager`
would live -- using the message *type* the bag itself declares, since a bag
has no publishers to negotiate QoS or attribute ownership with. `/tf` +
`/tf_static` get the same dual-topic wiring here that
`global_watches.subscribe_tf_watch` gives them live, so TF-watch sees the
whole tree instead of two unaware-of-each-other halves.

Reuses `SubscriptionManager`'s tier-assignment and message-processing
internals directly (a companion module, like `global_watches.py`) rather
than duplicating that logic for a second, bag-flavored code path.

Lifecycle-aware suppression isn't replayed: bags don't carry `~/get_state`
responses, and wiring up `~/transition_event`-driven suppression for a
batch pass is a reasonable follow-up, not core to this milestone -- a
topic from a since-deactivated node reports its raw liveness/content
status here rather than a suppressed one.
"""
from __future__ import annotations

import logging

import rclpy.node
import rosbag2_py
from rclpy.serialization import deserialize_message
from rosidl_runtime_py.utilities import get_message

from testudo.core.aggregator import HysteresisDebouncer
from testudo.core.clock import TestudoClock
from testudo.core.config import TestudoConfig
from testudo.core.global_watches import TF_STATIC_TOPIC, TF_TOPIC, TF_TYPE
from testudo.core.subscription_manager import SubscriptionManager, _FullTierState
from testudo.core.topic_report import TopicReport
from testudo.core.vitals import TopicVitals
from testudo.plugins.base import CheckStatus
from testudo.plugins.registry import DiscoveredPlugin

_logger = logging.getLogger(__name__)


class _ReplayClockSource:
    """A clock source whose `now()` reflects the latest bag timestamp fed to it."""

    def __init__(self) -> None:
        self.nanoseconds = 0

    def now(self) -> "_ReplayClockSource":
        return self

    def advance_to(self, nanoseconds: int) -> None:
        self.nanoseconds = max(self.nanoseconds, nanoseconds)


def _open_reader(bag_path: str) -> rosbag2_py.SequentialReader:
    reader = rosbag2_py.SequentialReader()
    storage_options = rosbag2_py.StorageOptions(uri=bag_path, storage_id="")
    converter_options = rosbag2_py.ConverterOptions(
        input_serialization_format="cdr", output_serialization_format="cdr"
    )
    reader.open(storage_options, converter_options)
    return reader


def _track_for_replay(manager: SubscriptionManager, topic_name: str, msg_type_str: str) -> None:
    """Assign a tier to `topic_name` without a live subscription.

    Mirrors `SubscriptionManager._subscribe_topic`'s tier decision and
    threshold/related-topics merging, minus the live-only parts (QoS
    negotiation, owning-node attribution, `create_subscription`).
    """
    if topic_name in manager._msg_type_by_topic:
        return
    manager._msg_type_by_topic[topic_name] = msg_type_str

    plugin_class = manager._plugin_class_by_msg_type.get(msg_type_str)
    if plugin_class is None:
        manager._vitals[topic_name] = TopicVitals(topic=topic_name)
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

    plugin = plugin_class(thresholds=thresholds, related_topics=related_topics)
    state = _FullTierState(
        plugin=plugin,
        vitals=TopicVitals(topic=topic_name),
        debouncer=HysteresisDebouncer(manager._hysteresis_required_consecutive),
    )
    manager._full_tier[topic_name] = state
    for related_topic_name in related_topics.values():
        manager._related_topic_targets[related_topic_name] = (plugin, topic_name)


def _feed_replayed_message(manager: SubscriptionManager, topic_name: str, msg_type_str: str, msg: object) -> None:
    """Feed one message (from a bag) through the same pipeline a live subscription would apply.

    Uses `manager._clock.now_seconds()` for timing; the caller is expected
    to have advanced that clock to this message's recorded timestamp
    already, so liveness/decimation/hysteresis all reason in bag time.
    """
    target = manager._related_topic_targets.get(topic_name)
    if target is not None:
        plugin, _primary_topic = target
        now = manager._clock.now_seconds()
        plugin.on_tick(now)
        plugin.on_message(topic_name, msg)
        return

    _track_for_replay(manager, topic_name, msg_type_str)

    now = manager._clock.now_seconds()
    if topic_name in manager._vitals:
        manager._vitals[topic_name].record_arrival(now)
        return
    state = manager._full_tier.get(topic_name)
    if state is not None:
        manager._process_full_tier_message(state, topic_name, msg, now)


def _wire_tf_watch_for_replay(manager: SubscriptionManager, topic_types: dict[str, str]) -> None:
    """Route /tf_static to the same TF-watch plugin instance as /tf, bag-flavored.

    Mirrors `global_watches.subscribe_tf_watch`'s live wiring using the
    bag's upfront topic list instead of live discovery.
    """
    if TF_TOPIC not in topic_types or manager._plugin_class_by_msg_type.get(TF_TYPE) is None:
        return

    _track_for_replay(manager, TF_TOPIC, TF_TYPE)
    state = manager._full_tier.get(TF_TOPIC)
    if state is None:
        return

    if TF_STATIC_TOPIC in topic_types:
        manager._related_topic_targets[TF_STATIC_TOPIC] = (state.plugin, TF_TOPIC)

    if hasattr(state.plugin, "set_watched_pairs"):
        state.plugin.set_watched_pairs([(pair.parent, pair.child) for pair in manager._config.tf])


def replay_bag(
    bag_path: str,
    config: TestudoConfig,
    discovered_plugins: list[DiscoveredPlugin],
    node: rclpy.node.Node,
) -> tuple[list[TopicReport], CheckStatus]:
    """Replay every message in `bag_path` and return the final batch report + overall status.

    Raises:
        FileNotFoundError: `bag_path` doesn't exist or isn't a readable bag.
    """
    try:
        reader = _open_reader(bag_path)
    except RuntimeError as exc:
        raise FileNotFoundError(f"could not open bag '{bag_path}': {exc}") from exc

    topic_types = {topic.name: topic.type for topic in reader.get_all_topics_and_types()}

    clock_source = _ReplayClockSource()
    clock = TestudoClock(clock_source, sim_time_query=lambda: True)
    manager = SubscriptionManager(node, config, discovered_plugins, clock=clock)
    excluded = manager.excluded_topics()

    _wire_tf_watch_for_replay(manager, topic_types)

    msg_classes: dict[str, type] = {}
    message_count = 0

    while reader.has_next():
        topic_name, data, timestamp_ns = reader.read_next()
        if topic_name in excluded:
            continue
        msg_type_str = topic_types.get(topic_name)
        if msg_type_str is None:
            continue

        msg_class = msg_classes.get(topic_name)
        if msg_class is None:
            try:
                msg_class = get_message(msg_type_str)
            except Exception:
                _logger.exception("could not resolve message class for '%s' (%s)", topic_name, msg_type_str)
                continue
            msg_classes[topic_name] = msg_class

        try:
            msg = deserialize_message(data, msg_class)
        except Exception:
            _logger.exception("could not deserialize a message on '%s'", topic_name)
            continue

        clock_source.advance_to(timestamp_ns)
        _feed_replayed_message(manager, topic_name, msg_type_str, msg)
        message_count += 1

    manager.tick()
    _logger.info("replayed %d message(s) from '%s'", message_count, bag_path)

    reports = manager.reports()
    overall = manager.overall_status(config.severity_mode)
    return reports, overall
