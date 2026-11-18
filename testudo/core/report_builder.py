"""`SubscriptionManager.reports()`: one TopicReport per tracked topic, whatever its tier.

A companion module like maintenance.py and tier_subscription.py: it reads
the manager's per-tier state, and turns each topic into a report with the
`topic_report` builders -- liveness, quiet-by-design flags (latched,
event-driven), idle inputs, publishers, and lifecycle suppression.
"""
from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from testudo.core.code_overrides import apply_code_overrides
from testudo.core.exclusion import matching_rule
from testudo.core.global_watches import TF_TOPIC
from testudo.core.nav2_rates import rate_problem as nav2_rate_problem
from testudo.core.passive_reports import (
    excluded_report,
    missing_type_report,
    no_publishers_report,
    presence_report,
)
from testudo.core.tier_subscription import run_check
from testudo.core.topic_report import (
    TopicReport,
    full_tier_report,
    is_event_driven,
    rate_metric,
    suppress_if_inactive,
    vitals_report,
    with_code,
)
from testudo.core.vitals import TopicVitals

from testudo.plugins.base import CheckStatus, Severity, SeverityLevels

if TYPE_CHECKING:
    from testudo.core.subscription_manager import SubscriptionManager
    from testudo.core.tier_subscription import FullTierState


def build_reports(manager: "SubscriptionManager", *, code_overrides: bool = True) -> list[TopicReport]:
    """Current health of every subscribed (or publisher-less) topic, sorted by name.

    `code_overrides=False` leaves out the config's per-topic `codes:`
    overrides: what dead-stack detection judges, since an override hides a
    code from the user, not the fact that the topic stopped.
    """
    now = manager._clock.now_seconds()
    reports: list[TopicReport] = []

    for topic_name in manager._no_publishers:
        idle_input = topic_name not in manager._had_publishers and not is_declared(manager, topic_name)
        reports.append(
            no_publishers_report(
                topic_name,
                manager._msg_type_by_topic[topic_name],
                idle_input,
                manager._subscribers_by_topic.get(topic_name, ()),
            )
        )
    for topic_name, msg_type in manager._unresolved.items():
        # An undeclared one stays out of the reports: nobody asked for it.
        if is_declared(manager, topic_name):
            reports.append(_suppress(manager, topic_name, missing_type_report(topic_name, msg_type)))
    for topic_name, msg_type in manager._presence_only.items():
        reports.append(_suppress(manager, topic_name, presence_report(topic_name, msg_type)))
    for topic_name, msg_type in manager._excluded.items():
        # No `_suppress`: an excluded topic never resolves an owning
        # node (no publisher-info query -- that's the whole point), so
        # lifecycle-aware suppression would always be a no-op for it.
        rule = matching_rule(manager, topic_name)
        rule_text = f"{rule.match_type.value} {rule.pattern}" if rule is not None else ""
        reports.append(excluded_report(topic_name, msg_type, rule_text))
    for topic_name, vitals in manager._vitals.items():
        report = vitals_report(
            topic_name,
            manager._msg_type_by_topic[topic_name],
            vitals,
            now,
            manager._stale_after_seconds,
            _rate_levels(manager, topic_name),
            manager._monitor_hz,
            **_quiet_flags(manager, topic_name, vitals_only=True),
            first_message_grace_s=manager._first_message_grace_seconds,
        )
        reports.append(_suppress(manager, topic_name, _with_nav2_rate(manager, report, vitals, now)))
    for topic_name, state in manager._full_tier.items():
        plugin_status = run_check(state, topic_name, state.plugin.get_status)
        report = full_tier_report(
            topic_name,
            manager._msg_type_by_topic[topic_name],
            state.vitals,
            state.debouncer.current,
            plugin_status or CheckStatus(severity=Severity.OK, label="check", message="the check failed"),
            now,
            manager._stale_after_seconds,
            manager._monitor_hz,
            **_quiet_flags(manager, topic_name),
            rate_levels=_rate_levels(manager, topic_name),
            first_message_grace_s=manager._first_message_grace_seconds,
        )
        report = _with_check_failure(report, state)
        reports.append(_suppress(manager, topic_name, _with_nav2_rate(manager, report, state.vitals, now)))
        reports.extend(_derived_reports(topic_name, state))

    if code_overrides:
        reports = [_override_codes(manager, report) for report in reports]
    return sorted(reports, key=lambda report: report.topic)


def _with_check_failure(report: TopicReport, state: "FullTierState") -> TopicReport:
    """`report` saying that its plugin raised: what it shows can't be trusted any more."""
    if state.check_failure is None:
        return report
    message = f"the check failed: {state.check_failure}"
    return replace(report, status=with_code(report.status, "CHECK-001", Severity.ERROR, message))


def _with_nav2_rate(manager: "SubscriptionManager", report: TopicReport, vitals: TopicVitals, now: float) -> TopicReport:
    """`report` judged against the rate Nav2's parameters ask for; a `rate_hz` in the config wins."""
    expected = manager._nav2_rates.expected.get(report.topic)
    if expected is None or not manager._monitor_hz or _rate_levels(manager, report.topic) is not None:
        return report
    status = report.status
    values = {**status.values, "expected_rate": f"{expected.hz:.3g} Hz ({expected.source})"}
    metrics = {**status.metrics, "expected_rate_hz": expected.hz} if status.metrics else status.metrics
    status = replace(status, values=values, metrics=metrics)
    problem = nav2_rate_problem(expected, vitals, now)
    return replace(report, status=with_code(status, "LIVE-004", *problem, rate_metric(report.rate_hz)) if problem is not None else status)


def _derived_reports(topic_name: str, state: "FullTierState") -> list[TopicReport]:
    """The extra rows a plugin works out from its topic (`derived_rows`, an optional hook)."""
    derived_rows = getattr(state.plugin, "derived_rows", None)
    if derived_rows is None:
        return []
    return [
        TopicReport(
            topic=row.name,
            msg_type=row.msg_type,
            tier="derived",
            status=row.status,
            plugin_group=row.plugin_group,
            source_topic=topic_name,
        )
        for row in derived_rows()
    ]


def _override_codes(manager: "SubscriptionManager", report: TopicReport) -> TopicReport:
    """`report` with its topic's `codes:` overrides applied (topic entry, then its action's)."""
    overrides: dict[str, int] = {}
    topic_name = report.source_topic or report.topic
    topic_config = manager._topic_config_by_name.get(topic_name)
    if topic_config is not None:
        overrides.update(topic_config.codes)
    action_config = manager._action_config_by_status_topic.get(topic_name)
    if action_config is not None:
        overrides.update(action_config.codes)
    if not overrides:
        return report
    return replace(report, status=apply_code_overrides(report.status, overrides))


def is_declared(manager: "SubscriptionManager", topic_name: str) -> bool:
    """True for a topic the config names: under `topics:`, an action's status, or /tf with `tf:` pairs."""
    return (
        topic_name in manager._topic_config_by_name
        or topic_name in manager._action_config_by_status_topic
        or (topic_name == TF_TOPIC and bool(manager._config.tf))
    )


def _rate_levels(manager: "SubscriptionManager", topic_name: str) -> SeverityLevels | None:
    """A declared topic's `thresholds.rate_hz` warn/error (LIVE-004), or None: rate is unchecked by default."""
    topic_config = manager._topic_config_by_name.get(topic_name)
    override = topic_config.levels.get("rate_hz") if topic_config is not None else None
    return override.over(None) if override is not None else None


def never_published_undeclared(manager: "SubscriptionManager", topic_name: str) -> bool:
    """True for a vitals-tier topic nobody asked for that hasn't sent a message yet.

    Nothing says it should have: debug and on-demand outputs are created at
    startup and stay silent until needed. Declaring the topic makes silence an error.
    """
    vitals = manager._vitals.get(topic_name)
    return vitals is not None and vitals.message_count == 0 and not is_declared(manager, topic_name)


def _quiet_flags(manager: "SubscriptionManager", topic_name: str, *, vitals_only: bool = False) -> dict[str, bool]:
    """`latched`/`event_driven` kwargs for a topic's liveness verdict (see `liveness_status`)."""
    topic_config = manager._topic_config_by_name.get(topic_name)
    msg_type = manager._msg_type_by_topic.get(topic_name)
    state = manager._full_tier.get(topic_name)
    event_driven = (
        is_event_driven(topic_name, msg_type)
        or (topic_config is not None and topic_config.event_driven)
        or (state is not None and state.plugin.silence_expected(msg_type or ""))
        or (vitals_only and never_published_undeclared(manager, topic_name))
    )
    return {"latched": topic_name in manager._latched, "event_driven": event_driven}


def _suppress(manager: "SubscriptionManager", topic_name: str, report: TopicReport) -> TopicReport:
    """`report` with its publishers attached, and lifecycle-suppressed if its node is inactive."""
    report = replace(report, publishers=manager._publishers_by_topic.get(topic_name, ()))
    return suppress_if_inactive(report, manager._owning_node_by_topic.get(topic_name), manager._lifecycle_tracker)
