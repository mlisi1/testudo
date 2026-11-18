"""Reports for topics Testudo tracks without a live subscription to judge.

No messages reach these, so there's no liveness or content verdict to
build: a topic nobody publishes, one left at the presence tier, one a user
excluded. Split out of topic_report.py, which covers the subscribed tiers.
"""
from __future__ import annotations

from testudo.core.topic_report import INPUT_LABEL, TopicReport
from testudo.plugins.base import CheckStatus, Severity, colorize


def no_publishers_report(
    topic_name: str, msg_type: str, idle_input: bool, subscribers: tuple[str, ...] = ()
) -> TopicReport:
    """A topic on the graph with no publisher: an ERROR, or an idle input when `idle_input`.

    The caller decides `idle_input`: true only for a topic that isn't
    declared and never had a publisher this session. A declared topic is
    expected to be published, and one that lost its publisher is a fault
    (a crashed driver leaves its consumers subscribed); otherwise a topic
    only subscribed to is an input nobody drives right now -- a goal, a
    teleop command, a speed limit -- or one this robot doesn't use at all.
    """
    if idle_input:
        values = {"subscribers": ", ".join(subscribers)} if subscribers else {}
        status = CheckStatus(
            severity=Severity.OK, label=INPUT_LABEL, message="input: subscribed, nobody publishing", values=values
        )
    else:
        status = CheckStatus(
            severity=Severity.ERROR,
            label="liveness",
            message="no publishers",
            codes={"LIVE-001": colorize("no publishers", Severity.ERROR)},
            code_severities={"LIVE-001": Severity.ERROR},
        )
    return TopicReport(topic=topic_name, msg_type=msg_type, tier="vitals", status=status)


def missing_type_report(topic_name: str, msg_type: str) -> TopicReport:
    """A declared topic whose message type can't be imported here (LIVE-005): asked for, but unmonitored.

    Its own label rather than "liveness": the topic may be perfectly alive,
    so this must not count toward dead-stack detection.
    """
    package = msg_type.split("/", 1)[0]
    message = f"not monitored: package {package} isn't installed"
    status = CheckStatus(
        severity=Severity.ERROR,
        label="missing-type",
        message=message,
        values={"missing_package": package},
        codes={"LIVE-005": colorize(message, Severity.ERROR)},
        code_severities={"LIVE-005": Severity.ERROR},
    )
    return TopicReport(topic=topic_name, msg_type=msg_type, tier="vitals", status=status)


def presence_report(topic_name: str, msg_type: str) -> TopicReport:
    """Build a presence-only report: a publisher exists, but the topic isn't subscribed at all.

    Used for message types heavy enough that even a raw vitals subscription
    isn't actually cheap (see `DEFAULT_PRESENCE_ONLY_MSG_TYPES` in
    subscription_manager.py) -- rather than pay for their data, Testudo
    settles for knowing a publisher is there, unless the topic is declared
    under `topics:` to opt into real monitoring.
    """
    message = "publisher present; not subscribed by default"
    values = {"not_subscribed": f"heavy type; declare under topics: -> {msg_type} to check content"}
    return TopicReport(
        topic=topic_name,
        msg_type=msg_type,
        tier="presence",
        status=CheckStatus(severity=Severity.OK, label="presence", message=message, values=values),
    )


def excluded_report(topic_name: str, msg_type: str, rule: str = "") -> TopicReport:
    """Build a bare report for a topic dropped by a user `exclude_topics` rule.

    Unlike `presence_report`, Testudo never resolves publisher info for an
    excluded topic either -- knowing its name (from graph discovery) is
    already more than the "no subscription of any kind" promise strictly
    requires, but it's what lets the TUI group these into their own
    category instead of the topic silently vanishing. The Topic Panel
    shows only the name for this tier; there's deliberately nothing else
    (status, rate) to show. `rule` (e.g. "regex ^/filter") is which
    exclude rule matched, so the user can find the one to delete.
    """
    values = {"excluded_by_rule": rule} if rule else {}
    return TopicReport(
        topic=topic_name,
        msg_type=msg_type,
        tier="excluded",
        status=CheckStatus(severity=Severity.OK, label="excluded", message="excluded from monitoring", values=values),
    )
