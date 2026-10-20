"""Unit tests for the category summary's severity-breakdown formatting."""
from __future__ import annotations

from testudo.plugins.base import Severity
from testudo.tui.widgets.category_summary import format_excluded_count, format_severity_breakdown


def test_format_severity_breakdown_empty_is_dash() -> None:
    assert format_severity_breakdown({}) == "-"


def test_format_severity_breakdown_single_severity() -> None:
    result = format_severity_breakdown({Severity.OK: 5})
    assert "✓5" in result
    assert "[green]" in result


def test_format_severity_breakdown_worst_first() -> None:
    counts = {Severity.OK: 3, Severity.ERROR: 1, Severity.WARN: 2}
    result = format_severity_breakdown(counts)
    # ERROR (worst present) should appear before WARN, which appears before OK.
    assert result.index("✗1") < result.index("▲2") < result.index("✓3")


def test_format_severity_breakdown_omits_zero_counts() -> None:
    result = format_severity_breakdown({Severity.OK: 2, Severity.ERROR: 0})
    assert "✗" not in result
    assert "✓2" in result


def test_format_severity_breakdown_all_four_severities() -> None:
    counts = {Severity.OK: 1, Severity.WARN: 1, Severity.ERROR: 1, Severity.STALE: 1}
    result = format_severity_breakdown(counts)
    assert result.index("■1") < result.index("✗1") < result.index("▲1") < result.index("✓1")


def test_format_excluded_count_is_a_plain_count_not_a_severity() -> None:
    assert format_excluded_count(5) == "[dim]5 topics[/dim]"
    assert format_excluded_count(1) == "[dim]1 topic[/dim]"


def test_matching_reports_keeps_all_on_a_category_match_else_matching_topics() -> None:
    from testudo.core.topic_report import TopicReport
    from testudo.plugins.base import CheckStatus, Severity
    from testudo.tui.widgets.category_summary import matching_reports

    reports = [
        TopicReport(t, "sensor_msgs/msg/Imu", "full", CheckStatus(Severity.OK, "s", "ok")) for t in ("/imu/data", "/imu/raw")
    ]
    assert matching_reports("IMU", reports, "") == reports
    assert matching_reports("IMU", reports, "imu") == reports
    assert [r.topic for r in matching_reports("IMU", reports, "raw")] == ["/imu/raw"]
    assert matching_reports("IMU", reports, "velodyne") == []
