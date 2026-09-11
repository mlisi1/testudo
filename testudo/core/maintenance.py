"""Live-session housekeeping: periodic rediscovery of new topics, and
detecting/clearing a stack that's gone away.

Split out of subscription_manager.py (a companion module, like
global_watches.py and topic_report.py) because "how do I subscribe once at
startup" and "how do I keep a long-running session honest about a graph
that keeps changing" are different concerns, even though the latter reuses
the former's per-topic subscribe logic. Every function here takes the
`SubscriptionManager` instance directly and reaches into its private state,
the same way global_watches.py does.

`discover_and_subscribe` backs both `SubscriptionManager.start()`'s initial
pass and `maintain()`'s periodic re-scans, so a topic that starts
publishing after Testudo does -- or an entire stack launched after Testudo
-- gets picked up without a restart. `maintain()` also watches for the
opposite case: once `stale_stack_fraction` of all current reports have
gone quiet (STALE, or stuck at "no messages"/"no publishers" -- see
`_mostly_dead`) continuously for `stale_stack_grace_seconds`, every tracked
topic (and its live subscription) is torn down, on the assumption the
observed stack was closed -- so the TUI stops showing dead rows and a
relaunched stack gets a clean rediscovery instead of stale leftovers mixed
in with new state.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from testudo.core.discovery import list_topics
from testudo.core.global_watches import subscribe_lifecycle_tracking, subscribe_tf_watch
from testudo.core.lifecycle import LifecycleTracker
from testudo.core.topic_report import TopicReport
from testudo.plugins.base import Severity

if TYPE_CHECKING:
    from testudo.core.subscription_manager import SubscriptionManager

_logger = logging.getLogger(__name__)

#: How often a live session re-scans the graph for topics that weren't
#: there yet at `start()` (or a whole stack started after Testudo did),
#: gated against wall-clock time so it keeps working even if
#: `use_sim_time` is active and the sim clock is paused or not yet ticking.
DEFAULT_REDISCOVERY_INTERVAL_SECONDS = 5.0

#: Fraction of *all* current reports that must count as "dead" (see
#: `_mostly_dead`) at once before a session starts the shutdown-grace
#: countdown (see `DEFAULT_STALE_STACK_GRACE_SECONDS`). Not 1.0: a topic or
#: two can be legitimately latched/low-rate without the rest of the stack
#: being down.
DEFAULT_STALE_STACK_FRACTION = 0.9

#: How long that dead fraction has to hold continuously (wall-clock) before
#: every tracked topic is torn down, on the assumption the observed stack
#: was closed. Comfortably longer than `stale_after_seconds` itself so a
#: brief graph hiccup can't trigger it.
DEFAULT_STALE_STACK_GRACE_SECONDS = 15.0

#: How often the dead-fraction check actually recomputes reports,
#: independent of how often `maintain()` is called -- `reports()` isn't
#: free (~one status evaluation per tracked topic), and `maintain()` may be
#: driven from a spin loop ticking far faster than this needs to be.
DEFAULT_STALE_CHECK_INTERVAL_SECONDS = 1.0


def discover_and_subscribe(manager: "SubscriptionManager") -> None:
    """Subscribe every currently-discovered, non-excluded topic not already committed.

    Shared by `SubscriptionManager.start()`'s initial pass and
    `maintain()`'s periodic re-scans. A topic previously recorded as having
    no publishers is retried here rather than left alone -- only topics
    actually committed to a tier, or reserved as another topic's
    related-topic companion, are skipped.
    """
    topics_by_name = {topic_info.name: topic_info for topic_info in list_topics(manager._node)}

    reserved = manager._reserved_related_topic_names()
    reserved |= subscribe_tf_watch(manager, topics_by_name)
    subscribe_lifecycle_tracking(manager, topics_by_name)

    committed = set(manager._msg_type_by_topic) - manager._no_publishers
    for name, topic_info in topics_by_name.items():
        if manager.is_default_excluded(name) or name in committed or name in reserved:
            continue
        if not topic_info.msg_types:
            _logger.warning("topic '%s' has no known message type; skipping", name)
            continue
        manager._subscribe_topic(name, topic_info.msg_types[0], topics_by_name)


def maintain(manager: "SubscriptionManager") -> None:
    """Periodic housekeeping for a live session: rediscover new topics, and clear a dead stack.

    No-op unless `start()` was called (`check`/`replay` construct a manager
    and drive `tick()` directly without it, so this stays inert for them).
    Must be called from the same thread that owns `manager._node`'s spin
    loop (`watch`'s background spin thread) -- rediscovery, stale-stack
    clearing, and the excluded-topic sweep below all create/destroy rclpy
    subscriptions, which isn't safe to interleave with a concurrent
    `rclpy.spin_once` running on another thread. `SubscriptionManager.tick()`
    stays callable from any thread, since it only touches plugin-internal
    state, never the node's subscriptions.
    """
    if not manager._live:
        return
    manager._sweep_excluded_topics()
    _maybe_clear_stale_stack(manager)
    _maybe_rediscover(manager)


def _maybe_rediscover(manager: "SubscriptionManager") -> None:
    """Re-run graph discovery if `rediscovery_interval_seconds` has elapsed (wall-clock)."""
    now_wall = time.monotonic()
    if (
        manager._last_rediscovery_monotonic is not None
        and (now_wall - manager._last_rediscovery_monotonic) < manager._rediscovery_interval_seconds
    ):
        return
    manager._last_rediscovery_monotonic = now_wall
    discover_and_subscribe(manager)


def _maybe_clear_stale_stack(manager: "SubscriptionManager") -> None:
    """If (almost) every topic has gone quiet for a while, assume the stack closed and clear.

    A robot's nav stack going down leaves every one of its topics stuck
    dead (STALE, or -- for a topic that never got a first message -- stuck
    at "no messages"/"no publishers" ERROR forever, see `_mostly_dead`),
    which would otherwise just accumulate as dead rows the TUI keeps
    showing. Once `stale_stack_fraction` of *all* current reports are dead
    continuously (wall-clock) for `stale_stack_grace_seconds`, every
    tracked topic (and its live subscription) is torn down via
    `_clear_all_topics`, so the next rediscovery pass starts clean once
    (if) the stack comes back -- rather than mixing fresh state in with
    stale leftovers.
    """
    now_wall = time.monotonic()
    if (
        manager._last_stale_check_monotonic is not None
        and (now_wall - manager._last_stale_check_monotonic) < manager._stale_check_interval_seconds
    ):
        return
    manager._last_stale_check_monotonic = now_wall

    reports = manager.reports()
    if not reports or not _mostly_dead(manager, reports):
        manager._stale_since_monotonic = None
        return
    if manager._stale_since_monotonic is None:
        manager._stale_since_monotonic = now_wall
        return
    if now_wall - manager._stale_since_monotonic >= manager._stale_stack_grace_seconds:
        _clear_all_topics(manager)
        manager._stale_since_monotonic = None


def _mostly_dead(manager: "SubscriptionManager", reports: list[TopicReport]) -> bool:
    """Fraction of `reports` that are liveness failures of any kind (not just literally STALE).

    A topic that never received a single message stays at ERROR
    ("no messages received") forever -- `liveness_status` checks
    `message_count == 0` before it ever looks at age, so that ERROR never
    transitions to STALE no matter how long nothing arrives. Same story for
    "no publishers". Counting only `Severity.STALE` here would under-count
    a genuinely dead stack whenever some of its topics happened to still be
    in that permanent zero-message ERROR state when it went down, so any
    liveness-derived report (identified by `label == "liveness"`, the same
    tag `topic_report.py` already uses for exactly this class of status) at
    ERROR or worse counts as dead. A content-check failure from a plugin
    (e.g. bad covariance) uses its own label, not "liveness", and is
    deliberately excluded -- that's a topic actively receiving data and
    complaining about it, not a dead one.
    """
    dead_count = sum(
        1 for report in reports if report.status.label == "liveness" and report.status.severity >= Severity.ERROR
    )
    return (dead_count / len(reports)) >= manager._stale_stack_fraction


def _clear_all_topics(manager: "SubscriptionManager") -> None:
    """Tear down every tracked topic and its live subscription, resetting to a pre-`start()` state.

    Destroying the subscriptions (not just the bookkeeping dicts) matters
    if the observed stack restarts more than once in a single `watch`
    session -- otherwise each cycle would leave the previous cycle's
    now-orphaned subscriptions still registered underneath, double-
    processing messages once a new publisher shows up on the same topic
    name.
    """
    _logger.warning(
        "%.0f%%+ of topics have gone quiet (stale or never-received) for over %.0fs; "
        "assuming the observed stack was closed and clearing all tracked state",
        manager._stale_stack_fraction * 100,
        manager._stale_stack_grace_seconds,
    )
    for subscription in manager._subscriptions.values():
        manager._node.destroy_subscription(subscription)
    manager._subscriptions.clear()
    manager._vitals.clear()
    manager._full_tier.clear()
    manager._presence_only.clear()
    manager._excluded.clear()
    manager._msg_type_by_topic.clear()
    manager._owning_node_by_topic.clear()
    manager._no_publishers.clear()
    manager._related_topic_targets.clear()
    manager._lifecycle_tracker = LifecycleTracker()
    # Reach immediately for a rediscovery pass on the very next call to
    # maintain(), rather than waiting out the rest of the normal interval.
    manager._last_rediscovery_monotonic = None
