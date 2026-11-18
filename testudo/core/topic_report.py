"""Renders tracked topic state into the `TopicReport` the CLI/TUI consume.

Split out from subscription_manager.py so "what do I subscribe to and how"
and "how do I turn what I'm tracking into a status" stay separate concerns
-- the subscription manager doesn't need to know how a report is rendered,
just that it can ask for one.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from testudo.core.lifecycle import LifecycleTracker
from testudo.core.vitals import TopicVitals
from testudo.plugins.base import (
    ActiveCodes,
    CheckStatus, CodeMetric,
    Severity,
    SeverityLevels,
    code_severity,
    colorize,
    evaluate_levels,
)


@dataclass(frozen=True)
class TopicReport:
    """One subscribed topic's current health, from whichever tier it's in."""

    topic: str
    msg_type: str
    tier: str  # "vitals", "full", "presence", "excluded", or "derived"
    status: CheckStatus
    rate_hz: float | None = None
    #: Each publisher as seen when the topic was discovered
    #: (`discovery.describe_publisher`); empty when unknown (replay, an
    #: excluded topic, a topic with none).
    publishers: tuple[str, ...] = ()
    #: For a "derived" row (worked out from another topic): the plugin output
    #: it's published with, and the topic whose config (`codes:`) applies to it.
    plugin_group: str | None = None
    source_topic: str | None = None


#: Message types whose publishers only speak when something changes -- a
#: behavior tree's status transitions, an action server's goal status, a
#: planner's path, a costmap's incremental updates -- so silence means
#: "nothing is happening", not "the publisher died". Extended per topic
#: with `event_driven: true` in config (e.g. a controller's cmd_vel,
#: silent whenever the robot isn't driving).
EVENT_DRIVEN_MSG_TYPES = frozenset(
    {
        "nav2_msgs/msg/BehaviorTreeLog",
        "action_msgs/msg/GoalStatusArray",
        "nav_msgs/msg/Path",
        "map_msgs/msg/OccupancyGridUpdate",
        "nav2_msgs/msg/CostmapUpdate",
        # What a node draws or explains only while it has something to show.
        "visualization_msgs/msg/Marker",
        "visualization_msgs/msg/MarkerArray",
        "dwb_msgs/msg/LocalPlanEvaluation",
        # A bond's heartbeat only flows while the bond is up.
        "bond/msg/Status",
    }
)

#: Topic-name suffixes that are event-driven whatever their message type:
#: an action's feedback only flows while one of its goals is running.
EVENT_DRIVEN_TOPIC_SUFFIXES = ("/_action/feedback",)


def is_event_driven(topic_name: str, msg_type: str | None) -> bool:
    """True when `topic_name` is quiet by design by default (`EVENT_DRIVEN_MSG_TYPES`/`_TOPIC_SUFFIXES`)."""
    return msg_type in EVENT_DRIVEN_MSG_TYPES or topic_name.endswith(EVENT_DRIVEN_TOPIC_SUFFIXES)

#: `CheckStatus.label`s for a topic that is quiet by design rather than
#: dead: severity OK, and the TUI renders them with their own glyphs.
IDLE_LABEL = "idle"
LATCHED_LABEL = "latched"
#: A topic other nodes subscribe to that nobody publishes: an input nobody
#: is driving right now (/goal_pose, a teleop cmd_vel), not a dead output.
INPUT_LABEL = "input"
QUIET_LABELS = frozenset({IDLE_LABEL, LATCHED_LABEL, INPUT_LABEL})


def quiet_label(status: CheckStatus) -> str | None:
    """`IDLE_LABEL`/`LATCHED_LABEL` if `status` is an OK verdict on a topic quiet by design, else None.

    Covers both shapes `liveness_status`'s quiet verdict ends up in: the
    vitals tier's own label, and a full-tier content status carrying an
    `<label>_for_s` value (see `full_tier_report`).
    """
    if status.severity != Severity.OK:
        return None
    if status.label in QUIET_LABELS:
        return status.label
    for label in (LATCHED_LABEL, IDLE_LABEL):
        if f"{label}_for_s" in status.values:
            return label
    return None


def liveness_status(
    vitals: TopicVitals,
    now: float,
    stale_after_seconds: float,
    monitor_hz: bool = True,
    *,
    latched: bool = False,
    event_driven: bool = False,
) -> CheckStatus | None:
    """The liveness-tier verdict, or None if the topic is alive and not stale.

    A None return means the caller should fall through to that topic's own
    content status; liveness problems always take priority over content
    checks, since content can't be trusted without live data.

    `monitor_hz=False` only affects the reported `rate_hz` value (see
    `vitals_values`) -- staleness itself is judged on message *age*, not
    rate, so disabling Hz monitoring never masks a genuinely dead topic.

    Two kinds of topic are quiet by design, and get an OK status labeled
    `QUIET_LABELS` instead of STALE: a `latched` one (transient_local
    publisher, typically sending once -- robot_description, camera
    extrinsics), and an `event_driven` one (see `EVENT_DRIVEN_MSG_TYPES`),
    which also isn't an error before its first message.
    """
    if vitals.message_count == 0:
        if event_driven:
            return CheckStatus(
                severity=Severity.OK,
                label=IDLE_LABEL,
                message="no messages yet (event-driven: publishes only when something happens)",
                values=vitals_values(vitals, None, monitor_hz),
            )
        message = "publisher(s) present but no messages received"
        return CheckStatus(
            severity=Severity.ERROR,
            label="liveness",
            message=message,
            codes={"LIVE-002": colorize(message, Severity.ERROR)},
            code_severities={"LIVE-002": Severity.ERROR},
        )
    age = vitals.age_seconds(now)
    if age is not None and age > stale_after_seconds:
        if latched:
            return CheckStatus(
                severity=Severity.OK,
                label=LATCHED_LABEL,
                message=f"latched: {vitals.message_count} message(s), transient_local",
                values=vitals_values(vitals, age, monitor_hz),
            )
        if event_driven:
            return CheckStatus(
                severity=Severity.OK,
                label=IDLE_LABEL,
                message=f"idle for {age:.1f}s (event-driven)",
                values=vitals_values(vitals, age, monitor_hz),
            )
        message = f"no message in the last {age:.1f}s"
        return CheckStatus(
            severity=Severity.STALE,
            label="liveness",
            message=message,
            values=vitals_values(vitals, age, monitor_hz),
            codes={"LIVE-003": colorize(message, Severity.STALE)},
            code_severities={"LIVE-003": Severity.STALE},
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


def vitals_metrics(vitals: TopicVitals, age: float | None, monitor_hz: bool = True) -> dict[str, float]:
    """`vitals_values` as raw numbers, for a full-tier status's published `metrics`; unknowns left out."""
    metrics: dict[str, float] = {"message_count": vitals.message_count}
    rate = vitals.rate_hz() if monitor_hz else None
    if rate is not None:
        metrics["rate_hz"] = rate
    if age is not None:
        metrics["age_s"] = age
    return metrics


def vitals_report(
    topic_name: str,
    msg_type: str,
    vitals: TopicVitals,
    now: float,
    stale_after_seconds: float,
    rate_levels: SeverityLevels | None = None,
    monitor_hz: bool = True,
    *,
    latched: bool = False,
    event_driven: bool = False,
) -> TopicReport:
    """Build a vitals-tier report: liveness verdict, or "alive" (optionally rate-checked).

    `rate_levels`, when given (from a declared topic's `thresholds.rate_hz:
    {warn: .., error: ..}` in config), checks the observed rate against it --
    lower is worse. This
    is how "actual vs. configured rate" checks (e.g. a costmap/planner/
    controller publishing slower than expected) work: no dedicated plugin,
    just a threshold on the vitals tier every topic already has.

    `monitor_hz=False` (the `--no-hz` flag) skips the rate-threshold
    comparison entirely and reports no rate on the topic, regardless of
    whether a threshold is configured -- arrival timestamps are still
    recorded (staleness needs them), just not turned into a rate.

    `latched`/`event_driven`: see `liveness_status`.
    """
    liveness = liveness_status(
        vitals, now, stale_after_seconds, monitor_hz, latched=latched, event_driven=event_driven
    )
    if liveness is not None:
        reported_rate = vitals.rate_hz() if monitor_hz else None
        return TopicReport(topic=topic_name, msg_type=msg_type, tier="vitals", status=liveness, rate_hz=reported_rate)
    age = vitals.age_seconds(now)
    rate = vitals.rate_hz() if monitor_hz else None
    values = vitals_values(vitals, age, monitor_hz)

    severity = Severity.OK
    message = "alive"
    codes = ActiveCodes()
    slow = rate_problem(rate, rate_levels, latched=latched, event_driven=event_driven)
    if slow is not None:
        severity, message = slow
        codes.add("LIVE-004", message, severity)

    status = CheckStatus(
        severity=severity,
        label="liveness",
        message=message,
        values=values,
        codes=codes.messages,
        code_severities=codes.severities,
        code_metrics={"LIVE-004": rate_metric(rate)} if slow is not None else {},
    )
    return TopicReport(topic=topic_name, msg_type=msg_type, tier="vitals", status=status, rate_hz=rate)


def rate_metric(rate: float | None) -> CodeMetric | None:
    """The number behind LIVE-004: lower is worse."""
    return CodeMetric("rate_hz", rate, higher_is_worse=False) if rate is not None else None


def rate_problem(
    rate: float | None, rate_levels: SeverityLevels | None, *, latched: bool = False, event_driven: bool = False
) -> tuple[int, str] | None:
    """(severity, message) when `rate` is below a configured `rate_hz` level (LIVE-004), else None.

    Lower is worse. Never for a quiet-by-design topic (latched, event-driven),
    whose rate says nothing about its health, nor without a rate (`--no-hz`,
    too few messages yet). Costs nothing: the rate is already tracked for
    every subscribed topic.
    """
    if rate is None or rate_levels is None or latched or event_driven:
        return None
    severity = evaluate_levels(rate, rate_levels, higher_is_worse=False)
    if severity == Severity.OK:
        return None
    limit = rate_levels.error if severity >= Severity.ERROR else rate_levels.warn
    return severity, f"rate {rate:.2f} Hz below {limit:g} Hz"


def full_tier_report(
    topic_name: str,
    msg_type: str,
    vitals: TopicVitals,
    debounced_status: CheckStatus | None,
    fallback_status: CheckStatus,
    now: float,
    stale_after_seconds: float,
    monitor_hz: bool = True,
    *,
    latched: bool = False,
    event_driven: bool = False,
    rate_levels: SeverityLevels | None = None,
) -> TopicReport:
    """Build a full-tier report: liveness verdict takes priority, else the (debounced) content status.

    `rate_levels` (a declared topic's `rate_hz`) adds LIVE-004 next to the
    plugin's own codes when the topic is alive but slower than configured
    (`rate_problem`); the topic takes the worse of the two severities.

    A topic that is quiet by design (`latched`/`event_driven`, see
    `liveness_status`) keeps its content status as-is -- a Nav2 action
    with a poor success rate is still a problem while it sits idle --
    with how long it has been quiet added to its values. A genuinely
    stale or silent topic gets the liveness verdict, but keeps the
    plugin's last-known values (and Topic Panel column) underneath it:
    what the topic last reported is exactly what's worth seeing when it
    stops reporting.

    A plugin that publishes `metrics` gets the topic's own rate/count/age
    (and how long it's been quiet) added to them, so they're plottable
    beside the plugin's numbers.
    """
    liveness = liveness_status(
        vitals, now, stale_after_seconds, monitor_hz, latched=latched, event_driven=event_driven
    )
    reported_rate = vitals.rate_hz() if monitor_hz else None
    content = debounced_status if debounced_status is not None else fallback_status
    age = vitals.age_seconds(now)
    metrics = {**vitals_metrics(vitals, age, monitor_hz), **content.metrics} if content.metrics else {}
    if liveness is None:
        status = replace(content, metrics=metrics) if metrics else content
        slow = rate_problem(reported_rate, rate_levels, latched=latched, event_driven=event_driven)
        if slow is not None:
            status = with_code(status, "LIVE-004", *slow, rate_metric(reported_rate))
    elif liveness.label in QUIET_LABELS:
        quiet = {f"{liveness.label}_for_s": f"{age:.1f}"} if age is not None else {}
        quiet_metrics = {f"{liveness.label}_for_s": age} if metrics and age is not None else {}
        status = replace(content, values={**content.values, **quiet}, metrics={**metrics, **quiet_metrics})
    else:
        status = replace(
            liveness,
            values={**liveness.values, **content.values},
            topic_panel_column=content.topic_panel_column,
            topic_panel_value=content.topic_panel_value,
            metrics=metrics,
            picture=content.picture,
        )
    return TopicReport(topic=topic_name, msg_type=msg_type, tier="full", status=status, rate_hz=reported_rate)


def with_code(
    status: CheckStatus, code: str, severity: int, message: str, metric: CodeMetric | None = None
) -> CheckStatus:
    """`status` with one more active `code` (and the `metric` behind it); its severity becomes the worse of the two."""
    codes = ActiveCodes(dict(status.codes), {c: code_severity(status, c) for c in status.codes})
    codes.add(code, message, severity)
    return replace(
        status,
        severity=max(status.severity, severity),
        codes=codes.messages,
        code_severities=codes.severities,
        code_metrics={**status.code_metrics, code: metric} if metric is not None else status.code_metrics,
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
    return replace(report, status=status)
