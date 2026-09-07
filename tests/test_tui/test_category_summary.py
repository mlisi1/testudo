"""Unit tests for the category summary's severity-breakdown formatting."""
from __future__ import annotations

from testudo.plugins.base import Severity
from testudo.tui.widgets.category_summary import format_severity_breakdown


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
