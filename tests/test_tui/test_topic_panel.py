"""Unit tests for TopicDetailTable's pure marquee-window and rate-formatting logic."""
from __future__ import annotations

from testudo.core.topic_report import TopicReport
from testudo.plugins.base import CheckStatus, Severity
from testudo.tui.widgets.topic_panel import format_rate, marquee_window, next_marquee_offset, status_cell


def test_format_rate_none_is_a_dash() -> None:
    assert format_rate(None) == "-"


def test_format_rate_formats_to_one_decimal() -> None:
    assert format_rate(12.345) == "12.3"


def test_format_rate_keeps_three_significant_digits_within_five_chars() -> None:
    assert format_rate(9.876) == "9.88"
    assert format_rate(200.4) == "200"
    assert format_rate(1234.0) == "1.23k"
    assert format_rate(28926.23) == "28.9k"
    assert format_rate(2.5e6) == "2.50M"


def test_marquee_window_returns_text_unchanged_when_it_fits() -> None:
    assert marquee_window("/odom", width=16, offset=0) == "/odom"


def test_marquee_window_slices_from_offset() -> None:
    text = "/local_costmap/costmap"  # 23 chars
    assert marquee_window(text, width=16, offset=0) == text[:16]
    assert marquee_window(text, width=16, offset=7) == text[7:23]


def test_next_marquee_offset_advances_forward() -> None:
    offset, direction = next_marquee_offset(text_length=23, width=16, offset=0, direction=1)
    assert offset == 1
    assert direction == 1


def test_next_marquee_offset_bounces_at_the_end() -> None:
    # max_offset = 23 - 16 = 7
    offset, direction = next_marquee_offset(text_length=23, width=16, offset=7, direction=1)
    assert offset == 7
    assert direction == -1


def test_next_marquee_offset_bounces_at_the_start() -> None:
    offset, direction = next_marquee_offset(text_length=23, width=16, offset=0, direction=-1)
    assert offset == 0
    assert direction == 1


def test_next_marquee_offset_ping_pongs_a_full_cycle() -> None:
    length, width = 20, 16  # max_offset = 4
    offset, direction = 0, 1
    seen = [offset]
    for _ in range(12):
        offset, direction = next_marquee_offset(length, width, offset, direction)
        seen.append(offset)
    # Bounces between 0 and 4 without ever going out of bounds.
    assert min(seen) == 0
    assert max(seen) == 4
    assert all(0 <= o <= 4 for o in seen)


def _report(tier: str, severity: Severity, label: str = "plugin", values: dict | None = None) -> TopicReport:
    status = CheckStatus(severity=severity, label=label, message="", values=values or {})
    return TopicReport(topic="/x", msg_type="t", tier=tier, status=status)


def test_status_cell_shows_severity_by_default() -> None:
    assert "WARN" in status_cell(_report("full", Severity.WARN))


def test_status_cell_quiet_topics_get_their_own_glyph() -> None:
    assert "◌ idle" in status_cell(_report("vitals", Severity.OK, label="idle"))
    assert "● latched" in status_cell(_report("vitals", Severity.OK, label="latched"))
    assert "◌ idle" in status_cell(_report("full", Severity.OK, values={"idle_for_s": "3.0"}))


def test_status_cell_presence_reads_as_present_not_ok() -> None:
    assert "◌ present" in status_cell(_report("presence", Severity.OK, label="presence"))


def test_status_cell_a_problem_outranks_quiet() -> None:
    assert "ERROR" in status_cell(_report("full", Severity.ERROR, values={"idle_for_s": "3.0"}))
