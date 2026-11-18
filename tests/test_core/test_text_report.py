"""The replay text report: synthetic reports and faults, no bag."""
from __future__ import annotations

import pytest

from testudo.core.fault_history import FaultEntry
from testudo.core.text_report import fault_line, print_report
from testudo.core.topic_report import TopicReport
from testudo.plugins.base import CheckStatus, Severity


def _fault(active: bool, times: int = 1) -> FaultEntry:
    return FaultEntry(
        topic="/gps/filtered",
        code="GNSS-002",
        active=active,
        severity=Severity.ERROR,
        current_severity=Severity.ERROR if active else Severity.OK,
        message="[red]position σ high[/red]",
        raised_at=12.0,
        cleared_at=None if active else 40.5,
        duration_s=28.5 if not active else 143.0,
        times_raised=times,
    )


def test_fault_line_gives_bag_times_duration_and_count() -> None:
    assert fault_line(_fault(active=False, times=3)).plain == (
        "ERROR GNSS-002 /gps/filtered  0:12.0 -> 0:40.5 (lasted 28.5 s), 3 times  position σ high"
    )
    assert fault_line(_fault(active=True)).plain == (
        "ERROR GNSS-002 /gps/filtered  0:12.0 -> still active at the end (143.0 s)  position σ high"
    )


def test_report_lists_faults_before_the_verdict_and_exits_on_the_end_state(capsys: pytest.CaptureFixture) -> None:
    reports = [TopicReport("/gps/filtered", "sensor_msgs/msg/NavSatFix", "full", CheckStatus(Severity.OK, "gnss", "fix"))]
    overall = CheckStatus(Severity.OK, "overall", "1 topic(s): OK=1")

    exit_code = print_report(reports, overall, [_fault(active=False)])

    out = capsys.readouterr().out
    assert exit_code == 0  # the fault cleared before the end
    assert out.index("Faults during the bag:") < out.index("GNSS-002") < out.index("[OK]")


def test_report_without_faults_has_no_faults_section(capsys: pytest.CaptureFixture) -> None:
    reports = [TopicReport("/a", "t", "vitals", CheckStatus(Severity.OK, "x", "ok"))]
    print_report(reports, CheckStatus(Severity.OK, "overall", "ok"), [])
    assert "Faults during the bag" not in capsys.readouterr().out
