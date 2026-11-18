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
from pathlib import Path
from typing import Callable

import rclpy.node
import rosbag2_py
import yaml
from rclpy.serialization import deserialize_message
from rosidl_runtime_py.utilities import get_message

from testudo.core.aggregator import HysteresisDebouncer
from testudo.core.clock import TestudoClock
from testudo.core.config import TestudoConfig
from testudo.core.global_watches import TF_STATIC_TOPIC, TF_TOPIC, TF_TYPE
from testudo.core.exclusion import maybe_record_excluded
from testudo.core.plugin_settings import build_plugin
from testudo.core.subscription_manager import SubscriptionManager
from testudo.core.stamp_check import stamp_check_for
from testudo.core.tier_subscription import (
    FullTierState,
    add_related_listener,
    feed_related_listeners,
    has_own_subscription,
    process_full_tier_message,
)
from testudo.core.topic_report import TopicReport
from testudo.core.vitals import TopicVitals
from testudo.plugins.base import CheckStatus
from testudo.plugins.registry import DiscoveredPlugin

_logger = logging.getLogger(__name__)


class ReplayClockSource:
    """A clock source whose `now()` reflects the latest bag timestamp fed to it."""

    def __init__(self) -> None:
        self.nanoseconds = 0

    def now(self) -> "ReplayClockSource":
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


def _latched_topics(bag_path: str) -> set[str]:
    """Topics recorded from a transient_local publisher, per the bag's `metadata.yaml`.

    rosbag2_py's `QoS` binding exposes durability only as a setter, so the
    offered profiles are read from the metadata file instead. Best-effort:
    a missing/unreadable file (or an unfamiliar layout) just means no topic
    is treated as latched -- the same STALE verdict replay gave before.
    """
    try:
        with open(Path(bag_path) / "metadata.yaml", encoding="utf-8") as metadata_file:
            info = yaml.safe_load(metadata_file)["rosbag2_bagfile_information"]
        latched: set[str] = set()
        for entry in info.get("topics_with_message_count", []):
            metadata = entry["topic_metadata"]
            profiles = metadata.get("offered_qos_profiles") or []
            if isinstance(profiles, str):  # pre-v9 bags store the list as an embedded YAML string
                profiles = yaml.safe_load(profiles) or []
            if any(profile.get("durability") in ("transient_local", 1) for profile in profiles):
                latched.add(metadata["name"])
        return latched
    except Exception:
        _logger.debug("could not read offered QoS from '%s' metadata", bag_path, exc_info=True)
        return set()


def _track_for_replay(manager: SubscriptionManager, topic_name: str, msg_type_str: str) -> None:
    """Assign a tier to `topic_name` without a live subscription.

    Mirrors `tier_subscription.subscribe_topic`'s tier decision and
    threshold/related-topics merging, minus the live-only parts (QoS
    negotiation, owning-node attribution, `create_subscription`).
    """
    if topic_name in manager._msg_type_by_topic:
        return
    if maybe_record_excluded(manager, topic_name, msg_type_str):
        return
    manager._msg_type_by_topic[topic_name] = msg_type_str

    if msg_type_str in manager._presence_only_msg_types and topic_name not in manager._topic_config_by_name:
        manager._presence_only[topic_name] = msg_type_str
        return

    plugin_class = manager._plugin_class_by_msg_type.get(msg_type_str)
    if plugin_class is None:
        manager._vitals[topic_name] = TopicVitals(topic=topic_name)
        return

    topic_config = manager._topic_config_by_name.get(topic_name)
    action_config = manager._action_config_by_status_topic.get(topic_name)
    related_topics: dict[str, str] = topic_config.related_topics if topic_config is not None else {}
    plugin = build_plugin(plugin_class, related_topics, topic_config, action_config)
    state = FullTierState(
        plugin=plugin,
        vitals=TopicVitals(topic=topic_name),
        debouncer=HysteresisDebouncer(manager._hysteresis_required_consecutive),
        stamps=stamp_check_for(topic_config, judge_other_time_base=False),
    )
    manager._full_tier[topic_name] = state
    for related_topic_name in related_topics.values():
        add_related_listener(manager, related_topic_name, plugin, topic_name)


def _feed_replayed_message(manager: SubscriptionManager, topic_name: str, msg_type_str: str, msg: object) -> None:
    """Feed one message (from a bag) through the same pipeline a live subscription would apply.

    Uses `manager._clock.now_seconds()` for timing; the caller is expected
    to have advanced that clock to this message's recorded timestamp
    already, so liveness/decimation/hysteresis all reason in bag time.
    """
    now = manager._clock.now_seconds()
    if not has_own_subscription(manager, topic_name):
        if topic_name in manager._related_topic_targets:
            feed_related_listeners(manager, topic_name, msg, now)
            return
        if topic_name in manager._reserved_related_topic_names():
            return  # its listener's first message is still to come

    _track_for_replay(manager, topic_name, msg_type_str)

    if topic_name in manager._vitals:
        manager._vitals[topic_name].record_arrival(now)
        feed_related_listeners(manager, topic_name, msg, now)
        return
    state = manager._full_tier.get(topic_name)
    if state is not None:
        process_full_tier_message(manager, state, topic_name, msg, now)


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
        add_related_listener(manager, TF_STATIC_TOPIC, state.plugin, TF_TOPIC)
        # What the live path passes to the plugin's constructor: how it
        # tells a static edge (never aged) from a dynamic one.
        state.plugin.related_topics = {**state.plugin.related_topics, "tf_static": TF_STATIC_TOPIC}

    if hasattr(state.plugin, "set_watched_pairs"):
        state.plugin.set_watched_pairs([(pair.parent, pair.child) for pair in manager._config.tf])


#: Called with every message's recorded time, *before* that message is fed
#: (see `replay_timeline.FrameRecorder`): the manager, the replay clock it
#: reads, and the timestamp in nanoseconds.
BagTimeHook = Callable[[SubscriptionManager, ReplayClockSource, int], None]


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
    manager = run_replay(bag_path, config, discovered_plugins, node)
    return manager.reports(), manager.overall_status(config.severity_mode)


def bag_duration_s(bag_path: str) -> float | None:
    """The bag's recorded duration from its metadata, or None when it can't be read."""
    try:
        duration = _open_reader(bag_path).get_metadata().duration
    except Exception:
        return None
    # A datetime.timedelta on recent rosbag2_py, a Duration with `nanoseconds` on older ones.
    if hasattr(duration, "total_seconds"):
        return duration.total_seconds()
    nanoseconds = getattr(duration, "nanoseconds", None)
    return None if nanoseconds is None else nanoseconds / 1e9


def run_replay(
    bag_path: str,
    config: TestudoConfig,
    discovered_plugins: list[DiscoveredPlugin],
    node: rclpy.node.Node,
    on_bag_time: BagTimeHook | None = None,
) -> SubscriptionManager:
    """Feed every message in `bag_path` through a fresh manager, tick it once at the end, and return it.

    `on_bag_time` sees the bag's time advance message by message, so a
    caller can sample the manager mid-bag (`replay_timeline`).

    Raises:
        FileNotFoundError: `bag_path` doesn't exist or isn't a readable bag.
    """
    try:
        reader = _open_reader(bag_path)
    except RuntimeError as exc:
        raise FileNotFoundError(f"could not open bag '{bag_path}': {exc}") from exc

    topic_types = {topic.name: topic.type for topic in reader.get_all_topics_and_types()}

    clock_source = ReplayClockSource()
    clock = TestudoClock(clock_source, sim_time_query=lambda: True)
    manager = SubscriptionManager(node, config, discovered_plugins, clock=clock)
    manager._latched = _latched_topics(bag_path)

    _wire_tf_watch_for_replay(manager, topic_types)

    msg_classes: dict[str, type] = {}
    message_count = 0

    while reader.has_next():
        topic_name, data, timestamp_ns = reader.read_next()
        if on_bag_time is not None:
            on_bag_time(manager, clock_source, timestamp_ns)
        if manager.is_default_excluded(topic_name):
            continue
        msg_type_str = topic_types.get(topic_name)
        if msg_type_str is None:
            continue
        # Recorded (once) and skipped here, before ever deserializing -- an
        # excluded topic (e.g. a heavy PointCloud2 someone dropped via
        # `exclude_topics`) shouldn't pay bag-replay's deserialization cost
        # on every single one of its messages just to be thrown away.
        if maybe_record_excluded(manager, topic_name, msg_type_str):
            continue

        # Logged once, then every later message is skipped without retrying
        # the lookup: a bag can hold tens of thousands of them on such a topic.
        if topic_name in manager._unresolved:
            continue
        msg_class = msg_classes.get(topic_name)
        if msg_class is None:
            try:
                msg_class = get_message(msg_type_str)
            except Exception:
                # Routine (a bag or robot using a package not installed here): one
                # line, not a traceback per topic; `-v` still shows the traceback.
                _logger.warning(
                    "could not resolve message class for '%s' (%s): its interface package isn't installed",
                    topic_name,
                    msg_type_str,
                    exc_info=_logger.isEnabledFor(logging.DEBUG),
                )
                manager._unresolved[topic_name] = msg_type_str
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
    return manager
