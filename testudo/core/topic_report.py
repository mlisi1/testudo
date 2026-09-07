"""Renders tracked topic state into the `TopicReport` the CLI/TUI consume.

Split out from subscription_manager.py so "what do I subscribe to and how"
and "how do I turn what I'm tracking into a status" stay separate concerns
-- the subscription manager doesn't need to know how a report is rendered,
just that it can ask for one.
"""
from __future__ import annotations

from dataclasses import dataclass

from testudo.core.lifecycle import LifecycleTracker
from testudo.core.vitals import TopicVitals
from testudo.plugins.base import CheckStatus, Severity, ThresholdZone, evaluate_zone


@dataclass(frozen=True)
class TopicReport:
    """One subscribed topic's current health, from whichever tier it's in."""

    topic: str
    msg_type: str
    tier: str  # "vitals" or "full"
    status: CheckStatus


def liveness_status(vitals: TopicVitals, now: float, stale_after_seconds: float) -> CheckStatus | None:
    """The liveness-tier verdict, or None if the topic is alive and not stale.

    A None return means the caller should fall through to that topic's own
    content status; liveness problems always take priority over content
    checks, since content can't be trusted without live data.
    """
    if vitals.message_count == 0:
        return CheckStatus(
            severity=Severity.ERROR, label="liveness", message="publisher(s) present but no messages received"
        )
    age = vitals.age_seconds(now)
    if age is not None and age > stale_after_seconds:
        return CheckStatus(
            severity=Severity.STALE,
            label="liveness",
            message=f"no message in the last {age:.1f}s",
            values=vitals_values(vitals, age),
        )
    return None


def vitals_values(vitals: TopicVitals, age: float | None) -> dict[str, str]:
    """The message_count/rate_hz/age_s trio every liveness-derived status reports."""
    rate = vitals.rate_hz()
    return {
        "message_count": str(vitals.message_count),
        "rate_hz": f"{rate:.2f}" if rate is not None else "n/a",
        "age_s": f"{age:.2f}" if age is not None else "n/a",
    }


def vitals_report(
    topic_name: str,
    msg_type: str,
    vitals: TopicVitals,
    now: float,
    stale_after_seconds: float,
    rate_threshold: ThresholdZone | None = None,
) -> TopicReport:
    """Build a vitals-tier report: liveness verdict, or "alive" (optionally rate-checked).

    `rate_threshold`, when given (from a declared topic's `thresholds.rate_hz`
    in config), checks the observed rate against it -- lower is worse. This
    is how "actual vs. configured rate" checks (e.g. a costmap/planner/
    controller publishing slower than expected) work: no dedicated plugin,
    just a threshold on the vitals tier every topic already has.
    """
    liveness = liveness_status(vitals, now, stale_after_seconds)
    if liveness is not None:
        return TopicReport(topic=topic_name, msg_type=msg_type, tier="vitals", status=liveness)
    age = vitals.age_seconds(now)
    rate = vitals.rate_hz()
    values = vitals_values(vitals, age)

    severity = Severity.OK
    message = "alive"
    if rate_threshold is not None and rate is not None:
        severity = evaluate_zone(rate, rate_threshold, higher_is_worse=False)
        if severity != Severity.OK:
            message = f"rate {rate:.2f}Hz below configured threshold"

    status = CheckStatus(severity=severity, label="liveness", message=message, values=values)
    return TopicReport(topic=topic_name, msg_type=msg_type, tier="vitals", status=status)


def full_tier_report(
    topic_name: str,
    msg_type: str,
    vitals: TopicVitals,
    debounced_status: CheckStatus | None,
    fallback_status: CheckStatus,
    now: float,
    stale_after_seconds: float,
) -> TopicReport:
    """Build a full-tier report: liveness verdict takes priority, else the (debounced) content status."""
    liveness = liveness_status(vitals, now, stale_after_seconds)
    if liveness is not None:
        return TopicReport(topic=topic_name, msg_type=msg_type, tier="full", status=liveness)
    status = debounced_status if debounced_status is not None else fallback_status
    return TopicReport(topic=topic_name, msg_type=msg_type, tier="full", status=status)


def suppress_if_inactive(
    report: TopicReport, owning_node: str | None, lifecycle_tracker: LifecycleTracker
) -> TopicReport:
    """Replace `report`'s status with a suppressed one if its owning node is unconfigured/inactive/finalized.

    `owning_node=None` (no active publisher to attribute the topic to, so
    its lifecycle state can't be determined) leaves the report untouched --
    a real, documented limitation, not a bug: a topic with zero publishers
    can't be traced back to a specific node this way.
    """
    if owning_node is None:
        return report
    state = lifecycle_tracker.state_of(owning_node)
    if not lifecycle_tracker.is_suppressed(owning_node):
        return report
    status = CheckStatus(
        severity=Severity.OK,
        label="suppressed",
        message=f"node '{owning_node}' is {state}; diagnostics suppressed",
        values={"lifecycle_state": state or ""},
    )
    return TopicReport(topic=report.topic, msg_type=report.msg_type, tier=report.tier, status=status)
