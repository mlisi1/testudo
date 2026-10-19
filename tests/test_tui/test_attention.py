"""Needs-attention list: synthetic reports, no app needed."""
from __future__ import annotations

from testudo.core.topic_report import TopicReport
from testudo.plugins.base import CheckStatus, Severity
from testudo.tui.widgets.attention import attention_entries, attention_line, attention_text


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
