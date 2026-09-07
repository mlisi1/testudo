"""Unit tests for aggregate_reports -- synthetic TopicReports, no ROS graph needed."""
from __future__ import annotations

from testudo.core.aggregator import aggregate_reports
from testudo.core.config import SeverityMode
from testudo.core.topic_report import TopicReport
from testudo.plugins.base import CheckStatus, Severity


def _report(topic: str, severity: int) -> TopicReport:
    return TopicReport(topic=topic, msg_type="t", tier="vitals", status=CheckStatus(severity=severity, label="x", message="m"))


def _unit_weight(topic: str) -> float:
    return 1.0


def test_empty_reports_is_ok() -> None:
    overall = aggregate_reports([], _unit_weight, SeverityMode.WORST)
    assert overall.severity == Severity.OK
    assert "no topics" in overall.message


def test_worst_mode_uses_max_severity() -> None:
    reports = [_report("/a", Severity.OK), _report("/b", Severity.ERROR), _report("/c", Severity.WARN)]
    overall = aggregate_reports(reports, _unit_weight, SeverityMode.WORST)
    assert overall.severity == Severity.ERROR
    assert "weighted_score" not in overall.values


def test_worst_mode_message_summarizes_counts() -> None:
    reports = [_report("/a", Severity.OK), _report("/b", Severity.OK), _report("/c", Severity.ERROR)]
    overall = aggregate_reports(reports, _unit_weight, SeverityMode.WORST)
    assert "OK=2" in overall.message
    assert "ERROR=1" in overall.message
    assert overall.values["topics_checked"] == "3"


def test_weighted_mode_rounds_weighted_average() -> None:
    # Equal weights: (OK=0, WARN=1, WARN=1, ERROR=2) -> mean 1.0 -> WARN.
    reports = [_report("/a", Severity.OK), _report("/b", Severity.WARN), _report("/c", Severity.WARN), _report("/d", Severity.ERROR)]
    overall = aggregate_reports(reports, _unit_weight, SeverityMode.WEIGHTED)
    assert overall.severity == Severity.WARN
    assert overall.values["weighted_score"] == "1"


def test_weighted_mode_respects_per_topic_weight() -> None:
    weights = {"/critical": 10.0, "/minor": 1.0}
    reports = [_report("/critical", Severity.ERROR), _report("/minor", Severity.OK)]
    overall = aggregate_reports(reports, lambda t: weights.get(t, 1.0), SeverityMode.WEIGHTED)
    # (2*10 + 0*1) / 11 = 1.818 -> rounds to ERROR(2)
    assert overall.severity == Severity.ERROR


def test_both_mode_uses_worst_but_includes_weighted_score() -> None:
    reports = [_report("/a", Severity.OK), _report("/b", Severity.ERROR)]
    overall = aggregate_reports(reports, _unit_weight, SeverityMode.BOTH)
    assert overall.severity == Severity.ERROR  # worst, not the weighted-rounded value
    assert overall.values["weighted_score"] == "1"  # (0+2)/2 = 1.0, informational only


def test_unweighted_topic_defaults_to_weight_one() -> None:
    reports = [_report("/unknown", Severity.WARN)]
    overall = aggregate_reports(reports, lambda t: 1.0, SeverityMode.WEIGHTED)
    assert overall.severity == Severity.WARN
