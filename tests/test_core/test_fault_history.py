"""FaultHistory: synthetic reports, no ROS graph."""
from __future__ import annotations

from testudo.core.fault_history import ClearedFault, FaultHistory, format_ago, latest_per_code
from testudo.core.topic_report import TopicReport
from testudo.plugins.base import CheckStatus, Severity


def _report(topic: str, severity: int = Severity.OK, codes: dict[str, str] | None = None) -> TopicReport:
    return TopicReport(topic, "pkg/msg/T", "full", CheckStatus(severity, "x", "", codes=codes or {}))


def test_a_fault_that_goes_away_is_recorded_with_its_worst_severity_and_last_message() -> None:
    history = FaultHistory()
    history.update([_report("/gps", Severity.WARN, {"GNSS-002": "first"})], now=10.0)
    history.update([_report("/gps", Severity.ERROR, {"GNSS-002": "worse"})], now=12.0)
    assert history.cleared() == ()
    history.update([_report("/gps")], now=15.0)
    assert history.cleared() == (ClearedFault("/gps", "GNSS-002", Severity.ERROR, "worse", 10.0, 15.0),)
    assert history.cleared()[0].lasted_s == 5.0


def test_codes_on_an_ok_report_are_not_faults() -> None:
    history = FaultHistory()
    history.update([_report("/imu", Severity.OK, {"X-001": "info"})], now=1.0)
    history.update([_report("/imu")], now=2.0)
    assert history.cleared() == ()


def test_a_topic_that_disappears_takes_its_faults_with_it_unrecorded() -> None:
    history = FaultHistory()
    history.update([_report("/cam", Severity.STALE, {"LIVE-003": "stale"})], now=1.0)
    history.update([], now=2.0)
    history.update([_report("/cam")], now=3.0)
    assert history.cleared() == ()


def test_cleared_faults_are_newest_first_bounded_and_reset_forgets_them() -> None:
    history = FaultHistory(max_cleared=2)
    for index, now in enumerate((1.0, 3.0, 5.0)):
        history.update([_report("/a", Severity.WARN, {f"C-{index}": "w"})], now=now)
        history.update([_report("/a")], now=now + 1)
    assert [fault.code for fault in history.cleared()] == ["C-2", "C-1"]
    history.update([_report("/a", Severity.WARN, {"C-9": "w"})], now=10.0)
    history.reset()
    history.update([_report("/a")], now=11.0)
    assert history.cleared() == ()


def test_latest_per_code_counts_a_flapping_code_once() -> None:
    faults = (
        ClearedFault("/a", "C-1", Severity.WARN, "", 8.0, 9.0),
        ClearedFault("/a", "C-2", Severity.ERROR, "", 6.0, 7.0),
        ClearedFault("/a", "C-1", Severity.WARN, "", 1.0, 2.0),
    )
    assert latest_per_code(faults) == [(faults[0], 2), (faults[1], 1)]


def test_format_ago_uses_one_unit() -> None:
    assert [format_ago(s) for s in (-1, 12.7, 125, 7300)] == ["0s", "12s", "2m", "2h"]


def test_entries_show_each_fault_once_active_or_as_its_latest_clearing() -> None:
    history = FaultHistory()
    history.update([_report("/gps", Severity.WARN, {"GNSS-002": "a"})], now=10.0)
    history.update([_report("/gps")], now=12.0)
    history.update([_report("/gps", Severity.ERROR, {"GNSS-002": "b"}), _report("/odom", Severity.WARN, {"LOC-001": "c"})], now=20.0)
    history.update([_report("/gps", Severity.ERROR, {"GNSS-002": "b"}), _report("/odom")], now=25.0)

    gps, odom = history.entries(now=30.0)
    assert (gps.topic, gps.active, gps.cleared_at, gps.duration_s, gps.times_raised) == ("/gps", True, None, 10.0, 2)
    assert (odom.topic, odom.active, odom.cleared_at, odom.duration_s, odom.times_raised) == ("/odom", False, 25.0, 5.0, 1)


def test_recent_retention_drops_cleared_faults_after_the_window_and_resets_their_count() -> None:
    from testudo.core.config import FaultRetention

    history = FaultHistory(retention=FaultRetention.RECENT, recent_window_seconds=60.0)
    history.update([_report("/a", Severity.WARN, {"C-1": "w"})], now=0.0)
    history.update([_report("/a")], now=1.0)
    history.update([_report("/a")], now=50.0)
    assert [entry.code for entry in history.entries(now=50.0)] == ["C-1"]
    history.update([_report("/a")], now=62.0)
    assert history.entries(now=62.0) == [] and history.cleared() == ()
    history.update([_report("/a", Severity.WARN, {"C-1": "w"})], now=70.0)
    assert history.entries(now=70.0)[0].times_raised == 1


def test_session_retention_keeps_old_cleared_faults() -> None:
    history = FaultHistory()
    history.update([_report("/a", Severity.WARN, {"C-1": "w"})], now=0.0)
    history.update([_report("/a")], now=1.0)
    history.update([_report("/a")], now=100_000.0)
    assert [entry.code for entry in history.entries(now=100_000.0)] == ["C-1"]


def test_a_fault_records_its_own_code_severity_not_its_topics() -> None:
    history = FaultHistory()
    status = CheckStatus(
        Severity.ERROR, "x", "", codes={"GNSS-001": "e", "GNSS-007": "w"}, code_severities={"GNSS-007": Severity.WARN}
    )
    history.update([TopicReport("/gps", "pkg/msg/T", "full", status)], now=1.0)
    history.update([_report("/gps")], now=2.0)
    assert {fault.code: fault.severity for fault in history.cleared()} == {
        "GNSS-001": Severity.ERROR,
        "GNSS-007": Severity.WARN,
    }
