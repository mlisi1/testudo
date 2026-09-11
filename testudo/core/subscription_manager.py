"""Orchestrates the two-tier subscription model.

Every topic on the graph gets subscribed to exactly once. A topic whose
message type is covered by a discovered plugin gets the *full* tier: a
typed subscription feeding that plugin's `on_message`, decimated to a
configurable max check rate on high-rate topics, with liveness (via a
`TopicVitals`) tracked from every message regardless of decimation, and its
content severity debounced (hysteresis) before being reported. A full-tier
topic declared in config may also list `related_topics` (e.g. a `cmd_vel`
companion for a cross-check); those get resolved and routed to the same
plugin instance rather than treated as topics of their own. Everything else
gets the *vitals* tier: a raw (undeserialized) subscription that only
tracks liveness/frequency, so the other 130-odd topics on a real robot stay
cheap to watch.

Two topics get dedicated, non-generic wiring instead of the type-matched
per-topic loop: `/tf` + `/tf_static` (they share a message type, so
type-matching alone would give them two plugin instances unaware of each
other instead of one that sees the whole tree) and every lifecycle node's
`~/transition_event` topic (pure side-channel bookkeeping, feeding
lifecycle-aware suppression -- it never gets a report row of its own).

A live session's `start()` is followed by ongoing `maintain()` calls (see
maintenance.py): periodic rediscovery for topics -- or a whole stack --
started after Testudo, and clearing all tracked state if the observed
stack looks like it's gone away. `maintain()` is deliberately separate
from `tick()` (which any thread may call): it mutates the node's
subscriptions, so it must run on whichever thread owns the spin loop.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import rclpy
import rclpy.node
from rosidl_runtime_py.utilities import get_message

from testudo.core.aggregator import DEFAULT_REQUIRED_CONSECUTIVE, HysteresisDebouncer, aggregate_reports
from testudo.core.clock import TestudoClock
from testudo.core.config import ActionConfig, ExcludeRule, SeverityMode, TestudoConfig, TopicConfig
from testudo.core.discovery import (
    NoPublishersError,
    TopicInfo,
    fully_qualified_node_name,
    resolve_publisher_info,
    resolve_subscription_qos,
)
from testudo.core.global_watches import DEFAULT_GET_STATE_TIMEOUT_SECONDS
from testudo.core.lifecycle import LifecycleTracker
from testudo.core.maintenance import (
    DEFAULT_REDISCOVERY_INTERVAL_SECONDS,
    DEFAULT_STALE_CHECK_INTERVAL_SECONDS,
    DEFAULT_STALE_STACK_FRACTION,
    DEFAULT_STALE_STACK_GRACE_SECONDS,
)
from testudo.core.maintenance import discover_and_subscribe as _discover_and_subscribe
from testudo.core.maintenance import maintain as _maintain
from testudo.core.topic_report import (
    TopicReport,
    excluded_report,
    full_tier_report,
    presence_report,
    suppress_if_inactive,
    vitals_report,
)
from testudo.core.vitals import DEFAULT_STALE_AFTER_SECONDS, TopicVitals
from testudo.plugins.base import CheckPlugin, CheckStatus, Severity, ThresholdZone, colorize
from testudo.plugins.registry import DiscoveredPlugin

_logger = logging.getLogger(__name__)

#: Topics excluded from subscription by default: ROS 2 graph bookkeeping,
#: not a useful diagnostics target.
DEFAULT_EXCLUDED_TOPICS = frozenset({"/parameter_events", "/rosout"})

#: Message types that default to the *presence* tier (see `presence_report`)
#: even though no discovered plugin covers them, so there's nowhere for a
#: `CheckPlugin.presence_only_msg_types()` declaration to live for them.
#: `theora_image_transport/msg/Packet`: `image_transport`'s compressed/
#: theora republishers are lazy publishers that only run their encoder once
#: something subscribes -- so testudo showing up and subscribing to every
#: topic can flip on an otherwise-idle, misconfigured encoder (observed in
#: the wild: a depth-image theora republisher fed to a color-only encoder,
#: erroring on every single frame once subscribed). Declaring a topic of
#: this type under `topics:` has no effect (no plugin covers it either
#: way) -- it stays presence-only regardless.
_PRESENCE_ONLY_MSG_TYPES_WITHOUT_PLUGIN = frozenset({"theora_image_transport/msg/Packet"})


def _index_presence_only_msg_types(plugins: list[DiscoveredPlugin]) -> frozenset[str]:
    """Union of every discovered plugin's `presence_only_msg_types()`, plus the no-plugin set above."""
    result: set[str] = set(_PRESENCE_ONLY_MSG_TYPES_WITHOUT_PLUGIN)
    for plugin in plugins:
        result.update(plugin.plugin_class.presence_only_msg_types())
    return frozenset(result)

#: A freshly created node's topic cache under-reports for a while after
#: creation, while discovery info from other graph participants is still
#: propagating in (observed ~0.5s in practice). There's no reliable "done"
#: signal to poll for -- a topic count that stops changing between two polls
#: might just mean nothing new arrived in that particular gap, not that
#: nothing is still in flight -- so `start()` waits this long unconditionally
#: before subscribing.
DEFAULT_GRAPH_SETTLE_SECONDS = 1.0

#: Full-tier content checks run at most this often per topic, regardless of
#: the topic's actual publish rate -- a 200 Hz IMU still only gets its
#: plugin's `on_message` called ~this many times a second. Liveness
#: (TopicVitals) still sees every message; only the (potentially expensive)
#: content check is decimated.
DEFAULT_MAX_CHECK_RATE_HZ = 10.0


@dataclass
class _FullTierState:
    """Per-topic bookkeeping for a full-tier subscription."""

    plugin: CheckPlugin
    vitals: TopicVitals
    debouncer: HysteresisDebouncer
    last_checked_seconds: float | None = field(default=None)


def _index_plugins_by_msg_type(plugins: list[DiscoveredPlugin]) -> dict[str, type[CheckPlugin]]:
    """Map each covered message type to a plugin class. First one found wins on overlap."""
    index: dict[str, type[CheckPlugin]] = {}
    for plugin in plugins:
        for msg_type in plugin.plugin_class.msg_types():
            index.setdefault(msg_type, plugin.plugin_class)
    return index


def _index_topic_configs_by_name(config: TestudoConfig) -> dict[str, TopicConfig]:
    """Flatten config.topics (keyed by msg type) into a lookup keyed by topic name."""
    return {topic_config.name: topic_config for entries in config.topics.values() for topic_config in entries}


def _index_action_configs_by_status_topic(config: TestudoConfig) -> dict[str, ActionConfig]:
    """Map each declared action's derived status topic name to its config, for threshold lookup.

    An action's `name` is conventionally written without a leading slash
    (e.g. "navigate_to_pose"), but the actual graph topic is absolute
    ("/navigate_to_pose/_action/status") -- normalize so the lookup matches
    regardless of which way the user wrote it.
    """
    return {f"/{action.name.lstrip('/')}/_action/status": action for action in config.actions}


class SubscriptionManager:
    """Subscribes to every non-excluded graph topic and tracks its health."""

    def __init__(
        self,
        node: rclpy.node.Node,
        config: TestudoConfig,
        discovered_plugins: list[DiscoveredPlugin],
        clock: TestudoClock | None = None,
        stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
        graph_settle_seconds: float = DEFAULT_GRAPH_SETTLE_SECONDS,
        max_check_rate_hz: float = DEFAULT_MAX_CHECK_RATE_HZ,
        hysteresis_required_consecutive: int = DEFAULT_REQUIRED_CONSECUTIVE,
        get_state_timeout_seconds: float = DEFAULT_GET_STATE_TIMEOUT_SECONDS,
        rediscovery_interval_seconds: float = DEFAULT_REDISCOVERY_INTERVAL_SECONDS,
        stale_stack_fraction: float = DEFAULT_STALE_STACK_FRACTION,
        stale_stack_grace_seconds: float = DEFAULT_STALE_STACK_GRACE_SECONDS,
        stale_check_interval_seconds: float = DEFAULT_STALE_CHECK_INTERVAL_SECONDS,
        spin_once: Callable[[Any, float], None] = rclpy.spin_once,
        monitor_hz: bool = True,
    ) -> None:
        self._node = node
        self._config = config
        self._clock = clock if clock is not None else TestudoClock.from_node(node)
        self._stale_after_seconds = stale_after_seconds
        # `--no-hz`: arrival timestamps are still recorded (staleness needs
        # them), but no rate is computed from them or checked against a
        # configured threshold -- see topic_report.py.
        self._monitor_hz = monitor_hz
        self._graph_settle_seconds = graph_settle_seconds
        self._max_check_rate_hz = max_check_rate_hz
        self._hysteresis_required_consecutive = hysteresis_required_consecutive
        self._get_state_timeout_seconds = get_state_timeout_seconds
        self._rediscovery_interval_seconds = rediscovery_interval_seconds
        self._stale_stack_fraction = stale_stack_fraction
        self._stale_stack_grace_seconds = stale_stack_grace_seconds
        self._stale_check_interval_seconds = stale_check_interval_seconds
        # Injectable so `~/get_state` service-response waiting (used to seed
        # lifecycle state at startup) is testable without a live rclpy node.
        self._spin_once = spin_once
        self._plugin_class_by_msg_type = _index_plugins_by_msg_type(discovered_plugins)
        self._presence_only_msg_types = _index_presence_only_msg_types(discovered_plugins)
        self._topic_config_by_name = _index_topic_configs_by_name(config)
        self._action_config_by_status_topic = _index_action_configs_by_status_topic(config)
        self._lifecycle_tracker = LifecycleTracker()

        self._vitals: dict[str, TopicVitals] = {}
        self._full_tier: dict[str, _FullTierState] = {}
        # topic name -> msg type, for undeclared topics of a
        # presence-only-by-default type (see DEFAULT_PRESENCE_ONLY_MSG_TYPES)
        # that never get a subscription at all.
        self._presence_only: dict[str, str] = {}
        # topic name -> msg type, for topics matching a user `exclude_topics`
        # rule -- no subscription, no publisher-info resolution, just enough
        # to render as a bare row in the TUI's own "Excluded Topics"
        # category (see topic_report.excluded_report). Distinct from the
        # DEFAULT_EXCLUDED_TOPICS/self-topic exclusion in `excluded_topics()`,
        # which stays fully invisible -- that's ROS graph bookkeeping, not
        # something a user chose to drop.
        self._excluded: dict[str, str] = {}
        self._msg_type_by_topic: dict[str, str] = {}
        self._owning_node_by_topic: dict[str, str] = {}
        self._no_publishers: set[str] = set()
        # topic name -> live Subscription handle, for every subscription this
        # manager holds (vitals, full-tier, related-topic, and lifecycle
        # transition_event alike -- the latter populated by
        # `global_watches.subscribe_lifecycle_tracking`, which reaches into
        # this dict the same way it does `_msg_type_by_topic`). Only needed
        # so maintenance.py's `_clear_all_topics` can destroy everything cleanly.
        self._subscriptions: dict[str, Any] = {}
        # related-topic name -> (owning plugin instance, its primary topic
        # name). Populated by `_subscribe_full_tier` and consulted by replay
        # (`feed_replayed_message`), which has no live-subscription callback
        # of its own to close over the plugin the way `_subscribe_related_topic` does.
        self._related_topic_targets: dict[str, tuple[CheckPlugin, str]] = {}
        # Only `start()` flips this on -- `replay`/one-shot `check` construct
        # a manager and drive `tick()` without ever calling `start()`'s live
        # discovery, so periodic rediscovery and stale-stack clearing (both
        # gated on this) must stay inert until a real live session opts in.
        self._live = False
        self._last_rediscovery_monotonic: float | None = None
        self._last_stale_check_monotonic: float | None = None
        self._stale_since_monotonic: float | None = None

    def excluded_topics(self) -> set[str]:
        """Default excludes plus self-exclusion of Testudo's own published diagnostics topic."""
        return set(DEFAULT_EXCLUDED_TOPICS) | {self._config.publish.topic}

    def is_default_excluded(self, topic_name: str) -> bool:
        """True for ROS graph bookkeeping/self-exclusion -- stays fully invisible, never an Excluded-category row."""
        return topic_name in self.excluded_topics()

    def _matching_exclude_rule(self, topic_name: str) -> ExcludeRule | None:
        return next((rule for rule in self._config.exclude_topics if rule.matches(topic_name)), None)

    def is_excluded(self, topic_name: str) -> bool:
        """True if `topic_name` gets no subscription of any kind -- default/self, or a user exclude rule."""
        return self.is_default_excluded(topic_name) or self._matching_exclude_rule(topic_name) is not None

    def exclude_rules(self) -> list[ExcludeRule]:
        """Every currently effective user exclude rule (config-loaded, plus anything added this session)."""
        return list(self._config.exclude_topics)

    def add_exclude_rule(self, rule: ExcludeRule) -> int | None:
        """Add `rule` to the effective rule set.

        Returns how many already-tracked topics it matches (a preview
        count for the caller's UI, e.g. the Options screen's "moved N
        topic(s)" message), or `None` if an identical rule was already in
        effect -- a no-op the caller uses to skip writing a duplicate
        line to the config file.

        Deliberately does *not* tear down any matched topic's live
        subscription itself: this may be called from any thread (the
        Options screen runs on the TUI's own thread, not the node's spin
        loop), and `destroy_subscription` isn't safe to interleave with a
        concurrent `spin_once` elsewhere. Appending to `_config.exclude_topics`
        is a plain Python list mutation -- safe from any thread -- and the
        actual move into the excluded tier happens on the next
        `maintain()` call instead, via `_sweep_excluded_topics`, which
        only ever runs on the thread that owns the spin loop. The delay
        is at most one `maintain()` cycle (driven every ~0.1s by `watch`'s
        spin thread), not user-perceptible.
        """
        if rule in self._config.exclude_topics:
            return None
        self._config.exclude_topics.append(rule)
        return sum(1 for name in self._msg_type_by_topic if name not in self._excluded and rule.matches(name))

    def remove_exclude_rule(self, rule: ExcludeRule) -> bool:
        """Remove a previously added exclude rule. Returns True if one was removed.

        A topic that was only excluded via this rule isn't resubscribed
        directly here -- it's dropped from `_excluded` (and its bookkeeping
        entry), so the next `discover_and_subscribe` pass picks it back up
        exactly like any newly-appeared topic. A topic still matched by a
        *different* remaining rule stays excluded.

        That next pass is forced to happen on the very next `maintain()`
        call (bypassing `rediscovery_interval_seconds`, same trick
        `maintenance._clear_all_topics` uses) rather than left to fall due
        on its own periodic schedule (5s by default): otherwise a topic
        evicted here is invisible in *every* category -- no longer
        excluded, not yet rediscovered as vitals/full -- for up to that
        whole interval. That gap is exactly what made a quick
        remove-then-re-add of the same rule from the Options screen look
        like the topic just vanished.
        """
        try:
            self._config.exclude_topics.remove(rule)
        except ValueError:
            return False
        stale = [n for n in self._excluded if self._matching_exclude_rule(n) is None]
        for name in stale:
            del self._excluded[name]
            del self._msg_type_by_topic[name]
        if stale:
            self._last_rediscovery_monotonic = None
        return True

    def _exclude_tracked_topic(self, topic_name: str) -> None:
        """Move an already-tracked topic into the excluded tier, tearing down its live subscription if any."""
        msg_type = self._msg_type_by_topic.get(topic_name)
        subscription = self._subscriptions.pop(topic_name, None)
        if subscription is not None:
            self._node.destroy_subscription(subscription)
        self._vitals.pop(topic_name, None)
        self._full_tier.pop(topic_name, None)
        self._presence_only.pop(topic_name, None)
        self._owning_node_by_topic.pop(topic_name, None)
        self._no_publishers.discard(topic_name)
        self._related_topic_targets.pop(topic_name, None)
        if msg_type is not None:
            self._excluded[topic_name] = msg_type

    def _sweep_excluded_topics(self) -> None:
        """Move any tracked topic newly matching an exclude rule into the excluded tier.

        Called from `maintain()` on every cycle (the node's spin-loop
        thread) -- see `add_exclude_rule`'s docstring for why this can't
        happen synchronously wherever a rule was added instead. A rule
        added since the last sweep (e.g. from the Options screen) is
        picked up here, cheaply: this is a no-op scan over already-known
        topic names unless something actually needs excluding.
        """
        for name in [
            n for n in list(self._msg_type_by_topic) if n not in self._excluded and self._matching_exclude_rule(n) is not None
        ]:
            self._exclude_tracked_topic(name)

    def _maybe_record_excluded(self, topic_name: str, msg_type_str: str) -> bool:
        """If `topic_name` matches a user exclude rule, record it (no subscription) and return True.

        Shared by the live discovery loop (`_subscribe_topic`) and replay's
        per-message loop, so an excluded topic costs the same next-to-
        nothing either way: one dict entry, never a subscription, never a
        publisher-info query, never a deserialized message.
        """
        if topic_name in self._excluded:
            return True
        if self._matching_exclude_rule(topic_name) is None:
            return False
        self._msg_type_by_topic[topic_name] = msg_type_str
        self._excluded[topic_name] = msg_type_str
        return True

    def start(self) -> None:
        """Subscribe to every currently-discovered, non-excluded topic, then arm live rediscovery.

        A topic used as another topic's `related_topics` companion (e.g. a
        cmd_vel cross-check input), or /tf_static, is reserved up front so
        the generic loop never subscribes it independently. After this
        initial pass, `maintain()` re-runs the same discovery periodically
        (see maintenance.py) so topics -- or a whole stack -- started after
        Testudo still get picked up.
        """
        self._wait_for_graph_settle()
        _discover_and_subscribe(self)
        self._last_rediscovery_monotonic = time.monotonic()
        self._live = True

    def _reserved_related_topic_names(self) -> set[str]:
        reserved: set[str] = set()
        for topic_config in self._topic_config_by_name.values():
            reserved.update(topic_config.related_topics.values())
        return reserved

    def _wait_for_graph_settle(self) -> None:
        """Give ROS graph discovery `graph_settle_seconds` to reach this node.

        A brand-new node's `get_topic_names_and_types()` under-reports for a
        while after creation. This can't be detected by polling for a
        stable count: the graph's own bookkeeping topics show up instantly
        and don't change again for a while, so a "stopped changing" check
        would return immediately, before a real publisher's discovery
        info -- still in flight -- ever arrives.
        """
        time.sleep(self._graph_settle_seconds)

    def _subscribe_topic(self, topic_name: str, msg_type_str: str, topics_by_name: dict[str, TopicInfo]) -> None:
        if self._maybe_record_excluded(topic_name, msg_type_str):
            return
        self._msg_type_by_topic[topic_name] = msg_type_str
        try:
            publisher_info = resolve_publisher_info(self._node, topic_name)
        except NoPublishersError:
            _logger.warning("topic '%s' has no publishers", topic_name)
            self._no_publishers.add(topic_name)
            return
        self._no_publishers.discard(topic_name)
        self._owning_node_by_topic[topic_name] = fully_qualified_node_name(
            publisher_info.node_namespace, publisher_info.node_name
        )

        if msg_type_str in self._presence_only_msg_types and topic_name not in self._topic_config_by_name:
            self._presence_only[topic_name] = msg_type_str
            return

        try:
            msg_class = get_message(msg_type_str)
        except Exception:
            _logger.exception("could not resolve message class for '%s' (%s)", topic_name, msg_type_str)
            return

        plugin_class = self._plugin_class_by_msg_type.get(msg_type_str)
        if plugin_class is None:
            self._subscribe_vitals_tier(topic_name, msg_class, publisher_info.qos_profile)
            return

        topic_config = self._topic_config_by_name.get(topic_name)
        action_config = self._action_config_by_status_topic.get(topic_name)
        thresholds = dict(plugin_class.default_thresholds())
        related_topics: dict[str, str] = {}
        if topic_config is not None:
            thresholds.update(topic_config.thresholds)
            related_topics = topic_config.related_topics
        if action_config is not None:
            thresholds.update(action_config.thresholds)
        self._subscribe_full_tier(
            topic_name, msg_class, publisher_info.qos_profile, plugin_class, thresholds, related_topics, topics_by_name
        )

    def _subscribe_vitals_tier(self, topic_name: str, msg_class: type, qos_profile) -> None:
        vitals = TopicVitals(topic=topic_name)
        self._vitals[topic_name] = vitals

        def _on_raw_message(_raw_bytes: bytes) -> None:
            vitals.record_arrival(self._clock.now_seconds())

        # raw=True: the middleware still needs the real message type to match
        # publishers, but the callback receives serialized bytes and never
        # deserializes -- that's the whole cost saving of this tier.
        self._subscriptions[topic_name] = self._node.create_subscription(
            msg_class, topic_name, _on_raw_message, qos_profile, raw=True
        )

    def _subscribe_full_tier(
        self,
        topic_name: str,
        msg_class: type,
        qos_profile,
        plugin_class: type[CheckPlugin],
        thresholds: dict[str, ThresholdZone],
        related_topics: dict[str, str],
        topics_by_name: dict[str, TopicInfo],
    ) -> _FullTierState:
        plugin = plugin_class(thresholds=thresholds, related_topics=related_topics)
        state = _FullTierState(
            plugin=plugin,
            vitals=TopicVitals(topic=topic_name),
            debouncer=HysteresisDebouncer(self._hysteresis_required_consecutive),
        )
        self._full_tier[topic_name] = state

        def _on_message(msg: object) -> None:
            self._process_full_tier_message(state, topic_name, msg, self._clock.now_seconds())

        self._subscriptions[topic_name] = self._node.create_subscription(msg_class, topic_name, _on_message, qos_profile)

        for related_topic_name in related_topics.values():
            self._related_topic_targets[related_topic_name] = (plugin, topic_name)
            self._subscribe_related_topic(related_topic_name, plugin, topics_by_name)

        return state

    def _process_full_tier_message(self, state: _FullTierState, topic_name: str, msg: object, now: float) -> None:
        """Vitals + decimated content-check for one full-tier message, at `now`.

        Shared by the live subscription callback and replay's
        `feed_replayed_message`, so both apply identical decimation,
        on_tick timing, and hysteresis debouncing.
        """
        state.vitals.record_arrival(now)
        min_check_interval = 1.0 / self._max_check_rate_hz if self._max_check_rate_hz > 0 else 0.0
        if state.last_checked_seconds is not None and (now - state.last_checked_seconds) < min_check_interval:
            return
        state.last_checked_seconds = now
        state.plugin.on_tick(now)
        state.plugin.on_message(topic_name, msg)
        state.debouncer.update(state.plugin.get_status())

    def _subscribe_related_topic(
        self, related_topic_name: str, plugin: CheckPlugin, topics_by_name: dict[str, TopicInfo]
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
            qos_profile = resolve_subscription_qos(self._node, related_topic_name)
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
            plugin.on_tick(self._clock.now_seconds())
            plugin.on_message(related_topic_name, msg)

        self._subscriptions[related_topic_name] = self._node.create_subscription(
            msg_class, related_topic_name, _on_related_message, qos_profile
        )

    def tick(self) -> None:
        """Advance every full-tier plugin's time-based state. Call on a fixed timer."""
        now = self._clock.now_seconds()
        for state in self._full_tier.values():
            state.plugin.on_tick(now)

    def maintain(self) -> None:
        """Periodic housekeeping for a live session: rediscover new topics, and clear a dead stack.

        No-op unless `start()` was called (`check`/`replay` construct a
        manager and drive `tick()` directly without it, so this stays
        inert for them). Must be called from the same thread that owns
        `self._node`'s spin loop (`watch`'s background spin thread) --
        see maintenance.py's `maintain()` for why. `tick()` itself stays
        callable from any thread (as it already was) since it only touches
        plugin-internal state, never the node's subscriptions.
        """
        _maintain(self)

    def reports(self) -> list[TopicReport]:
        """Current health of every subscribed (or publisher-less) topic, sorted by name."""
        now = self._clock.now_seconds()
        reports: list[TopicReport] = []

        for topic_name in self._no_publishers:
            reports.append(
                TopicReport(
                    topic=topic_name,
                    msg_type=self._msg_type_by_topic[topic_name],
                    tier="vitals",
                    status=CheckStatus(
                        severity=Severity.ERROR,
                        label="liveness",
                        message="no publishers",
                        codes={"LIVE-001": colorize("no publishers", Severity.ERROR)},
                    ),
                )
            )
        for topic_name, msg_type in self._presence_only.items():
            reports.append(self._suppress(topic_name, presence_report(topic_name, msg_type)))
        for topic_name, msg_type in self._excluded.items():
            # No `_suppress`: an excluded topic never resolves an owning
            # node (no publisher-info query -- that's the whole point), so
            # lifecycle-aware suppression would always be a no-op for it.
            reports.append(excluded_report(topic_name, msg_type))
        for topic_name, vitals in self._vitals.items():
            topic_config = self._topic_config_by_name.get(topic_name)
            rate_threshold = topic_config.thresholds.get("rate_hz") if topic_config is not None else None
            report = vitals_report(
                topic_name,
                self._msg_type_by_topic[topic_name],
                vitals,
                now,
                self._stale_after_seconds,
                rate_threshold,
                self._monitor_hz,
            )
            reports.append(self._suppress(topic_name, report))
        for topic_name, state in self._full_tier.items():
            report = full_tier_report(
                topic_name,
                self._msg_type_by_topic[topic_name],
                state.vitals,
                state.debouncer.current,
                state.plugin.get_status(),
                now,
                self._stale_after_seconds,
                self._monitor_hz,
            )
            reports.append(self._suppress(topic_name, report))

        return sorted(reports, key=lambda report: report.topic)

    def _suppress(self, topic_name: str, report: TopicReport) -> TopicReport:
        return suppress_if_inactive(report, self._owning_node_by_topic.get(topic_name), self._lifecycle_tracker)

    def _weight_for_topic(self, topic_name: str) -> float:
        """A topic's configured weight (declared topic or action status topic), default 1.0."""
        topic_config = self._topic_config_by_name.get(topic_name)
        if topic_config is not None:
            return topic_config.weight
        action_config = self._action_config_by_status_topic.get(topic_name)
        if action_config is not None:
            return action_config.weight
        return 1.0

    def overall_status(self, mode: SeverityMode) -> CheckStatus:
        """The aggregated (worst/weighted/both) status across every current report."""
        return aggregate_reports(self.reports(), self._weight_for_topic, mode)

    def reset_stats(self) -> None:
        """Clear accumulated rolling-window stats for every tracked topic, keeping subscriptions live.

        Backs the TUI's 'r' keybind. Deliberately doesn't touch config,
        tier assignment, or subscriptions -- only the history each vitals/
        plugin instance has accumulated -- so it's safe to call while a
        background spin thread may be concurrently delivering messages.
        """
        for vitals in self._vitals.values():
            vitals.reset()
        for state in self._full_tier.values():
            state.vitals.reset()
            state.plugin.reset()
            state.debouncer = HysteresisDebouncer(self._hysteresis_required_consecutive)
            state.last_checked_seconds = None
