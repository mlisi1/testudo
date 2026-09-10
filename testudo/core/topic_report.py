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
from testudo.plugins.base import CheckStatus, Severity, ThresholdZone, colorize, evaluate_zone


@dataclass(frozen=True)
class TopicReport:
    """One subscribed topic's current health, from whichever tier it's in."""

    topic: str
    msg_type: str
    tier: str  # "vitals", "full", or "presence"
    status: CheckStatus
    rate_hz: float | None = None


def liveness_status(
    vitals: TopicVitals, now: float, stale_after_seconds: float, monitor_hz: bool = True
) -> CheckStatus | None:
    """The liveness-tier verdict, or None if the topic is alive and not stale.

    A None return means the caller should fall through to that topic's own
    content status; liveness problems always take priority over content
    checks, since content can't be trusted without live data.

    `monitor_hz=False` only affects the reported `rate_hz` value (see
    `vitals_values`) -- staleness itself is judged on message *age*, not
    rate, so disabling Hz monitoring never masks a genuinely dead topic.
    """
    if vitals.message_count == 0:
        message = "publisher(s) present but no messages received"
        return CheckStatus(
            severity=Severity.ERROR,
            label="liveness",
            message=message,
            codes={"LIVE-002": colorize(message, Severity.ERROR)},
        )
    age = vitals.age_seconds(now)
    if age is not None and age > stale_after_seconds:
        message = f"no message in the last {age:.1f}s"
        return CheckStatus(
            severity=Severity.STALE,
            label="liveness",
            message=message,
            values=vitals_values(vitals, age, monitor_hz),
            codes={"LIVE-003": colorize(message, Severity.STALE)},
        )
    return None


def vitals_values(vitals: TopicVitals, age: float | None, monitor_hz: bool = True) -> dict[str, str]:
    """The message_count/rate_hz/age_s trio every liveness-derived status reports.

    `rate_hz` reads "off" rather than a computed value when `monitor_hz` is
    False (the `--no-hz` flag) -- distinct from "n/a", which still means
    "not enough samples yet".
    """
    rate = vitals.rate_hz()
    if not monitor_hz:
        rate_str = "off"
    else:
        rate_str = f"{rate:.2f}" if rate is not None else "n/a"
    return {
        "message_count": str(vitals.message_count),
        "rate_hz": rate_str,
        "age_s": f"{age:.2f}" if age is not None else "n/a",
    }


def vitals_report(
    topic_name: str,
    msg_type: str,
    vitals: TopicVitals,
    now: float,
    stale_after_seconds: float,
    rate_threshold: ThresholdZone | None = None,
    monitor_hz: bool = True,
) -> TopicReport:
    """Build a vitals-tier report: liveness verdict, or "alive" (optionally rate-checked).

    `rate_threshold`, when given (from a declared topic's `thresholds.rate_hz`
    in config), checks the observed rate against it -- lower is worse. This
    is how "actual vs. configured rate" checks (e.g. a costmap/planner/
    controller publishing slower than expected) work: no dedicated plugin,
    just a threshold on the vitals tier every topic already has.

    `monitor_hz=False` (the `--no-hz` flag) skips the rate-threshold
    comparison entirely and reports no rate on the topic, regardless of
    whether a threshold is configured -- arrival timestamps are still
    recorded (staleness needs them), just not turned into a rate.
    """
    liveness = liveness_status(vitals, now, stale_after_seconds, monitor_hz)
    if liveness is not None:
        reported_rate = vitals.rate_hz() if monitor_hz else None
        return TopicReport(topic=topic_name, msg_type=msg_type, tier="vitals", status=liveness, rate_hz=reported_rate)
    age = vitals.age_seconds(now)
    rate = vitals.rate_hz() if monitor_hz else None
    values = vitals_values(vitals, age, monitor_hz)

    severity = Severity.OK
    message = "alive"
    codes: dict[str, str] = {}
    if monitor_hz and rate_threshold is not None and rate is not None:
        severity = evaluate_zone(rate, rate_threshold, higher_is_worse=False)
        if severity != Severity.OK:
            message = f"rate {rate:.2f}Hz below configured threshold"
            codes["LIVE-004"] = colorize(message, severity)

    status = CheckStatus(severity=severity, label="liveness", message=message, values=values, codes=codes)
    return TopicReport(topic=topic_name, msg_type=msg_type, tier="vitals", status=status, rate_hz=rate)


def full_tier_report(
    topic_name: str,
    msg_type: str,
    vitals: TopicVitals,
    debounced_status: CheckStatus | None,
    fallback_status: CheckStatus,
    now: float,
    stale_after_seconds: float,
    monitor_hz: bool = True,
) -> TopicReport:
    """Build a full-tier report: liveness verdict takes priority, else the (debounced) content status."""
    liveness = liveness_status(vitals, now, stale_after_seconds, monitor_hz)
    reported_rate = vitals.rate_hz() if monitor_hz else None
    if liveness is not None:
        return TopicReport(topic=topic_name, msg_type=msg_type, tier="full", status=liveness, rate_hz=reported_rate)
    status = debounced_status if debounced_status is not None else fallback_status
    return TopicReport(topic=topic_name, msg_type=msg_type, tier="full", status=status, rate_hz=reported_rate)


def presence_report(topic_name: str, msg_type: str) -> TopicReport:
    """Build a presence-only report: a publisher exists, but the topic isn't subscribed at all.

    Used for message types heavy enough that even a raw vitals subscription
    isn't actually cheap (see `DEFAULT_PRESENCE_ONLY_MSG_TYPES` in
    subscription_manager.py) -- rather than pay for their data, Testudo
    settles for knowing a publisher is there, unless the topic is declared
    under `topics:` to opt into real monitoring.
    """
    message = "publisher present; not subscribed by default (heavy topic type -- declare under topics: to monitor)"
    return TopicReport(
        topic=topic_name,
        msg_type=msg_type,
        tier="presence",
        status=CheckStatus(severity=Severity.OK, label="presence", message=message),
    )


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
    return TopicReport(topic=report.topic, msg_type=report.msg_type, tier=report.tier, status=status, rate_hz=report.rate_hz)
