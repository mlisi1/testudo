"""User `exclude_topics` rules: matching, adding/removing live, and moving
topics into (and out of) the excluded tier.

Split out of subscription_manager.py (a companion module, like
maintenance.py and global_watches.py): the rule bookkeeping is its own
concern, shared by live discovery, periodic maintenance, and replay. Every
function here takes the `SubscriptionManager` instance directly and reaches
into its private state, the same way those modules do.

An excluded topic gets no subscription and no publisher-info query -- just
enough bookkeeping to render as a bare row in the TUI's own "Excluded
Topics" category (see `topic_report.excluded_report`). Distinct from the
DEFAULT_EXCLUDED_TOPICS/self-topic exclusion (`SubscriptionManager.
excluded_topics()`), which stays fully invisible -- that's ROS graph
bookkeeping, not something a user chose to drop.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from testudo.core.config import ExcludeRule

if TYPE_CHECKING:
    from testudo.core.subscription_manager import SubscriptionManager


def matching_rule(manager: SubscriptionManager, topic_name: str) -> ExcludeRule | None:
    """The first effective user exclude rule matching `topic_name`, if any."""
    return next((rule for rule in manager._config.exclude_topics if rule.matches(topic_name)), None)


def add_rule(manager: SubscriptionManager, rule: ExcludeRule) -> int | None:
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
    `maintain()` call instead, via `sweep_excluded_topics`, which
    only ever runs on the thread that owns the spin loop. The delay
    is at most one `maintain()` cycle (driven every ~0.1s by `watch`'s
    spin thread), not user-perceptible.
    """
    if rule in manager._config.exclude_topics:
        return None
    manager._config.exclude_topics.append(rule)
    return sum(1 for name in manager._msg_type_by_topic if name not in manager._excluded and rule.matches(name))


def remove_rule(manager: SubscriptionManager, rule: ExcludeRule) -> bool:
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
        manager._config.exclude_topics.remove(rule)
    except ValueError:
        return False
    stale = [n for n in manager._excluded if matching_rule(manager, n) is None]
    for name in stale:
        del manager._excluded[name]
        del manager._msg_type_by_topic[name]
    if stale:
        manager._last_rediscovery_monotonic = None
    return True


def exclude_tracked_topic(manager: SubscriptionManager, topic_name: str) -> None:
    """Move an already-tracked topic into the excluded tier, tearing down its live subscription if any."""
    msg_type = manager._msg_type_by_topic.get(topic_name)
    subscription = manager._subscriptions.pop(topic_name, None)
    if subscription is not None:
        manager._node.destroy_subscription(subscription)
    manager._vitals.pop(topic_name, None)
    manager._full_tier.pop(topic_name, None)
    manager._presence_only.pop(topic_name, None)
    manager._owning_node_by_topic.pop(topic_name, None)
    manager._no_publishers.discard(topic_name)
    manager._latched.discard(topic_name)
    manager._related_topic_targets.pop(topic_name, None)
    if msg_type is not None:
        manager._excluded[topic_name] = msg_type


def sweep_excluded_topics(manager: SubscriptionManager) -> None:
    """Move any tracked topic newly matching an exclude rule into the excluded tier.

    Called from `maintain()` on every cycle (the node's spin-loop
    thread) -- see `add_rule`'s docstring for why this can't happen
    synchronously wherever a rule was added instead. A rule added
    since the last sweep (e.g. from the Options screen) is picked up
    here, cheaply: this is a no-op scan over already-known topic names
    unless something actually needs excluding.
    """
    for name in [
        n
        for n in list(manager._msg_type_by_topic)
        if n not in manager._excluded and matching_rule(manager, n) is not None
    ]:
        exclude_tracked_topic(manager, name)


def maybe_record_excluded(manager: SubscriptionManager, topic_name: str, msg_type_str: str) -> bool:
    """If `topic_name` matches a user exclude rule, record it (no subscription) and return True.

    Shared by the live discovery loop (`tier_subscription.subscribe_topic`)
    and replay's per-message loop, so an excluded topic costs the same
    next-to-nothing either way: one dict entry, never a subscription,
    never a publisher-info query, never a deserialized message.
    """
    if topic_name in manager._excluded:
        return True
    if matching_rule(manager, topic_name) is None:
        return False
    manager._msg_type_by_topic[topic_name] = msg_type_str
    manager._excluded[topic_name] = msg_type_str
    return True
