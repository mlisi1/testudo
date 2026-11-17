"""Needs-attention list: synthetic reports, no app needed."""
from __future__ import annotations

from testudo.core.fault_history import ClearedFault
from testudo.core.topic_report import TopicReport
from testudo.plugins.base import CheckStatus, Severity
from testudo.tui.widgets.attention import attention_entries, attention_line, attention_text, problem_topics


def _report(topic: str, severity: int, codes: dict[str, str]) -> TopicReport:
    status = CheckStatus(severity=severity, label="x", message="", codes=codes)
    return TopicReport(topic=topic, msg_type="pkg/msg/T", tier="full", status=status, rate_hz=None)


def _plain(markup: str) -> str:
    from rich.text import Text

    return Text.from_markup(markup).plain


def test_entries_are_worst_first_and_skip_ok_reports() -> None:
    reports = [
        _report("/odom", Severity.WARN, {"ODOM-002": "w"}),
        _report("/gps", Severity.ERROR, {"GNSS-001": "e", "GNSS-003": "e"}),
        _report("/fine", Severity.OK, {"X-001": "info"}),
        _report("/b", Severity.STALE, {"LIVE-003": "s"}),
        _report("/a", Severity.STALE, {"LIVE-003": "s"}),
    ]
    assert attention_entries(reports) == [
        (Severity.STALE, "LIVE-003", ["/a", "/b"]),
        (Severity.ERROR, "GNSS-001", ["/gps"]),
        (Severity.ERROR, "GNSS-003", ["/gps"]),
        (Severity.WARN, "ODOM-002", ["/odom"]),
    ]


def test_line_keeps_the_topic_tail_within_width() -> None:
    line = _plain(attention_line(Severity.ERROR, "ODOM-001", ["/robot/odometry/gps"], 22))
    assert line == "✗ ODOM-001 …/gps"
    assert len(line) <= 22
    assert _plain(attention_line(Severity.STALE, "LIVE-003", ["/a", "/b"], 22)) == "■ LIVE-003 2 topics"


def test_text_hidden_without_codes_or_room() -> None:
    reports = [_report("/gps", Severity.ERROR, {"GNSS-001": "e"})]
    assert attention_text([], 30, 10) is None
    assert attention_text(reports, 30, 1) is None
    assert _plain(attention_text(reports, 30, 10)).splitlines() == ["Needs attention", "✗ GNSS-001 /gps"]


def test_text_caps_entries_and_counts_the_rest() -> None:
    reports = [_report(f"/t{i}", Severity.ERROR, {f"CODE-00{i}": "e"}) for i in range(8)]
    lines = _plain(attention_text(reports, 30, 20)).splitlines()
    assert len(lines) == 1 + 5 + 1
    assert lines[-1] == "+3 more"
    assert len(_plain(attention_text(reports, 30, 3)).splitlines()) == 3


def test_problem_topics_follow_the_list_order_once_each() -> None:
    reports = [
        _report("/odom", Severity.WARN, {"ODOM-002": "w"}),
        _report("/gps", Severity.ERROR, {"GNSS-001": "e", "GNSS-003": "e"}),
        _report("/fine", Severity.OK, {"X-001": "info"}),
        _report("/b", Severity.STALE, {"LIVE-003": "s"}),
        _report("/a", Severity.STALE, {"LIVE-003": "s"}),
    ]
    assert problem_topics(reports) == ["/a", "/b", "/gps", "/odom"]


def test_cleared_codes_fill_the_rows_the_active_ones_leave() -> None:
    reports = [_report("/gps", Severity.ERROR, {"GNSS-001": "e"})]
    cleared = (
        ClearedFault("/odom", "ODOM-002", Severity.WARN, "w", 50.0, 70.0),
        ClearedFault("/a", "LIVE-003", Severity.STALE, "s", 10.0, 40.0),
        ClearedFault("/b", "LIVE-003", Severity.STALE, "s", 10.0, 20.0),
    )
    lines = _plain(attention_text(reports, 30, 10, cleared, now=100.0)).splitlines()
    assert lines == [
        "Needs attention",
        "✗ GNSS-001 /gps",
        "",
        "Cleared",
        "▲ ODOM-002 30s /odom",
        "■ LIVE-003 1m 2 topics",
    ]
    # No room for the blank line, the title and one entry: the cleared list is left out.
    assert len(_plain(attention_text(reports, 30, 4, cleared, now=100.0)).splitlines()) == 2


def test_cleared_codes_show_on_their_own_when_nothing_is_active() -> None:
    cleared = (ClearedFault("/odom", "ODOM-002", Severity.WARN, "w", 50.0, 70.0),)
    assert _plain(attention_text([], 30, 5, cleared, now=75.0)).splitlines() == ["Cleared", "▲ ODOM-002 5s /odom"]


def test_a_warn_code_on_a_topic_in_error_is_listed_as_warn() -> None:
    status = CheckStatus(
        Severity.ERROR, "x", "", codes={"GNSS-001": "e", "GNSS-007": "w"}, code_severities={"GNSS-007": Severity.WARN}
    )
    reports = [TopicReport(topic="/gps", msg_type="pkg/msg/T", tier="full", status=status, rate_hz=None)]
    assert attention_entries(reports) == [(Severity.ERROR, "GNSS-001", ["/gps"]), (Severity.WARN, "GNSS-007", ["/gps"])]
