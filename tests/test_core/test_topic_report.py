"""Unit tests for topic_report's pure report-rendering functions."""
from __future__ import annotations

from testudo.core.lifecycle import LifecycleTracker
from testudo.core.topic_report import (
    IDLE_LABEL,
    LATCHED_LABEL,
    TopicReport,
    full_tier_report,
    quiet_label,
    suppress_if_inactive,
    vitals_report,
)
from testudo.core.vitals import TopicVitals
from testudo.plugins.base import CheckStatus, Severity, ThresholdZone


def _vitals_with_messages(count: int, arrival_seconds: list[float]) -> TopicVitals:
    vitals = TopicVitals(topic="/x")
    for t in arrival_seconds:
        vitals.record_arrival(t)
    return vitals


def test_vitals_report_no_messages_is_error() -> None:
    vitals = TopicVitals(topic="/x")
    report = vitals_report("/x", "std_msgs/msg/String", vitals, now=10.0, stale_after_seconds=2.0)
    assert report.status.severity == Severity.ERROR
    assert "no messages received" in report.status.message
    assert "LIVE-002" in report.status.codes


def test_vitals_report_stale_after_threshold() -> None:
    vitals = _vitals_with_messages(1, [0.0])
    report = vitals_report("/x", "std_msgs/msg/String", vitals, now=10.0, stale_after_seconds=2.0)
    assert report.status.severity == Severity.STALE
    assert "LIVE-003" in report.status.codes


def test_vitals_report_alive_without_rate_threshold_is_ok() -> None:
    vitals = _vitals_with_messages(2, [0.0, 0.1])
    report = vitals_report("/x", "std_msgs/msg/String", vitals, now=0.2, stale_after_seconds=2.0)
    assert report.status.severity == Severity.OK
    assert report.status.message == "alive"


def test_vitals_report_exposes_rate_hz_on_the_report_itself() -> None:
    """Hz is surfaced as a top-level field, independent of whatever the
    plugin/liveness status message happens to say -- this is what the
    Topic Panel's Hz column reads."""
    vitals = _vitals_with_messages(3, [0.0, 0.5, 1.0])  # 2 Hz
    report = vitals_report("/x", "std_msgs/msg/String", vitals, now=1.0, stale_after_seconds=5.0)
    assert report.rate_hz == 2.0


def test_vitals_report_rate_hz_is_none_with_no_messages() -> None:
    vitals = TopicVitals(topic="/x")
    report = vitals_report("/x", "std_msgs/msg/String", vitals, now=10.0, stale_after_seconds=2.0)
    assert report.rate_hz is None


def test_full_tier_report_exposes_rate_hz_regardless_of_content_status() -> None:
    vitals = _vitals_with_messages(3, [0.0, 0.5, 1.0])  # 2 Hz
    debounced = CheckStatus(severity=Severity.WARN, label="plugin", message="debounced")
    report = full_tier_report(
        "/x", "t", vitals, debounced_status=debounced, fallback_status=debounced, now=1.0, stale_after_seconds=5.0
    )
    assert report.rate_hz == 2.0


def test_vitals_report_rate_below_threshold_is_flagged() -> None:
    # Two messages 1s apart => 1 Hz, below both green(4.0) and orange(1.0)... actually 1.0<=orange -> WARN not ERROR.
    vitals = _vitals_with_messages(2, [0.0, 1.0])
    zone = ThresholdZone(green=4.0, orange=1.0)
    report = vitals_report("/x", "nav_msgs/msg/OccupancyGrid", vitals, now=1.0, stale_after_seconds=5.0, rate_threshold=zone)
    assert report.status.severity == Severity.WARN
    assert "below configured threshold" in report.status.message
    assert "LIVE-004" in report.status.codes


def test_vitals_report_alive_and_ok_has_no_active_codes() -> None:
    vitals = _vitals_with_messages(2, [0.0, 0.1])
    report = vitals_report("/x", "std_msgs/msg/String", vitals, now=0.2, stale_after_seconds=2.0)
    assert report.status.codes == {}


def test_vitals_report_rate_far_below_threshold_is_error() -> None:
    vitals = _vitals_with_messages(2, [0.0, 5.0])  # 0.2 Hz
    zone = ThresholdZone(green=4.0, orange=1.0)
    report = vitals_report("/x", "nav_msgs/msg/OccupancyGrid", vitals, now=5.0, stale_after_seconds=10.0, rate_threshold=zone)
    assert report.status.severity == Severity.ERROR


def test_vitals_report_rate_above_threshold_is_ok() -> None:
    vitals = _vitals_with_messages(3, [0.0, 0.2, 0.4])  # 5 Hz
    zone = ThresholdZone(green=4.0, orange=1.0)
    report = vitals_report("/x", "nav_msgs/msg/OccupancyGrid", vitals, now=0.4, stale_after_seconds=5.0, rate_threshold=zone)
    assert report.status.severity == Severity.OK


def test_full_tier_report_prefers_liveness_over_content() -> None:
    vitals = TopicVitals(topic="/x")
    fallback = CheckStatus(severity=Severity.OK, label="plugin", message="fine")
    report = full_tier_report("/x", "t", vitals, debounced_status=None, fallback_status=fallback, now=1.0, stale_after_seconds=2.0)
    assert report.status.severity == Severity.ERROR
    assert "no messages received" in report.status.message


def test_full_tier_report_uses_debounced_status_when_alive() -> None:
    vitals = _vitals_with_messages(1, [0.0])
    debounced = CheckStatus(severity=Severity.WARN, label="plugin", message="debounced")
    fallback = CheckStatus(severity=Severity.OK, label="plugin", message="fallback")
    report = full_tier_report("/x", "t", vitals, debounced_status=debounced, fallback_status=fallback, now=0.1, stale_after_seconds=2.0)
    assert report.status is debounced


def test_full_tier_report_uses_fallback_when_no_debounced_status_yet() -> None:
    vitals = _vitals_with_messages(1, [0.0])
    fallback = CheckStatus(severity=Severity.OK, label="plugin", message="fallback")
    report = full_tier_report("/x", "t", vitals, debounced_status=None, fallback_status=fallback, now=0.1, stale_after_seconds=2.0)
    assert report.status is fallback


def test_vitals_report_no_hz_skips_rate_threshold_even_when_configured() -> None:
    vitals = _vitals_with_messages(2, [0.0, 5.0])  # 0.2 Hz, well below the zone
    zone = ThresholdZone(green=4.0, orange=1.0)
    report = vitals_report(
        "/x", "nav_msgs/msg/OccupancyGrid", vitals, now=5.0, stale_after_seconds=10.0, rate_threshold=zone, monitor_hz=False
    )
    assert report.status.severity == Severity.OK
    assert report.rate_hz is None
    assert report.status.values["rate_hz"] == "off"


def test_vitals_report_no_hz_still_flags_staleness() -> None:
    vitals = _vitals_with_messages(1, [0.0])
    report = vitals_report("/x", "std_msgs/msg/String", vitals, now=10.0, stale_after_seconds=2.0, monitor_hz=False)
    assert report.status.severity == Severity.STALE
    assert report.status.values["rate_hz"] == "off"
    assert report.rate_hz is None


def test_full_tier_report_no_hz_reports_no_rate() -> None:
    vitals = _vitals_with_messages(2, [0.0, 0.1])
    fallback = CheckStatus(severity=Severity.OK, label="plugin", message="fine")
    report = full_tier_report(
        "/x", "t", vitals, debounced_status=None, fallback_status=fallback, now=0.1, stale_after_seconds=2.0, monitor_hz=False
    )
    assert report.rate_hz is None


def test_suppress_if_inactive_leaves_unknown_owner_untouched() -> None:
    tracker = LifecycleTracker()
    report = TopicReport("/x", "t", "vitals", CheckStatus(severity=Severity.ERROR, label="liveness", message="dead"))
    result = suppress_if_inactive(report, owning_node=None, lifecycle_tracker=tracker)
    assert result is report


def test_suppress_if_inactive_leaves_active_node_untouched() -> None:
    tracker = LifecycleTracker()
    tracker.on_transition_event("/controller_server", "active")
    report = TopicReport("/x", "t", "vitals", CheckStatus(severity=Severity.ERROR, label="liveness", message="dead"))
    result = suppress_if_inactive(report, owning_node="/controller_server", lifecycle_tracker=tracker)
    assert result is report


def test_suppress_if_inactive_replaces_status_for_inactive_node() -> None:
    tracker = LifecycleTracker()
    tracker.on_transition_event("/controller_server", "inactive")
    report = TopicReport("/x", "t", "vitals", CheckStatus(severity=Severity.ERROR, label="liveness", message="dead"))
    result = suppress_if_inactive(report, owning_node="/controller_server", lifecycle_tracker=tracker)
    assert result.status.severity == Severity.OK
    assert result.status.label == "suppressed"
    assert "inactive" in result.status.message
    assert result.topic == "/x"


def test_vitals_report_latched_topic_is_ok_not_stale() -> None:
    vitals = _vitals_with_messages(1, [0.0])
    report = vitals_report("/x", "std_msgs/msg/String", vitals, now=10.0, stale_after_seconds=2.0, latched=True)
    assert report.status.severity == Severity.OK
    assert report.status.label == LATCHED_LABEL
    assert not report.status.codes


def test_vitals_report_latched_topic_without_messages_is_still_an_error() -> None:
    """Latching only explains silence *after* a message -- a latched topic that never sent one is still broken."""
    report = vitals_report("/x", "t", TopicVitals(topic="/x"), now=10.0, stale_after_seconds=2.0, latched=True)
    assert report.status.severity == Severity.ERROR


def test_vitals_report_event_driven_topic_is_idle_before_and_after_messages() -> None:
    never = vitals_report("/x", "t", TopicVitals(topic="/x"), now=10.0, stale_after_seconds=2.0, event_driven=True)
    quiet = vitals_report(
        "/x", "t", _vitals_with_messages(1, [0.0]), now=10.0, stale_after_seconds=2.0, event_driven=True
    )
    assert never.status.severity == quiet.status.severity == Severity.OK
    assert never.status.label == quiet.status.label == IDLE_LABEL


def test_full_tier_report_idle_keeps_content_status_and_adds_idle_time() -> None:
    vitals = _vitals_with_messages(1, [0.0])
    content = CheckStatus(severity=Severity.WARN, label="plugin", message="low success rate", values={"rate": "50"})
    report = full_tier_report(
        "/x", "t", vitals, debounced_status=content, fallback_status=content, now=10.0, stale_after_seconds=2.0,
        event_driven=True,
    )
    assert report.status.severity == Severity.WARN
    assert report.status.values == {"rate": "50", "idle_for_s": "10.0"}
    assert quiet_label(report.status) is None  # a WARN is never shown as merely idle


def test_full_tier_report_idle_ok_content_reads_as_idle() -> None:
    vitals = _vitals_with_messages(1, [0.0])
    content = CheckStatus(severity=Severity.OK, label="plugin", message="fine")
    report = full_tier_report(
        "/x", "t", vitals, debounced_status=None, fallback_status=content, now=10.0, stale_after_seconds=2.0,
        event_driven=True,
    )
    assert quiet_label(report.status) == IDLE_LABEL


def test_full_tier_report_stale_keeps_last_known_content_values_and_column() -> None:
    vitals = _vitals_with_messages(1, [0.0])
    content = CheckStatus(
        severity=Severity.OK,
        label="plugin",
        message="fine",
        values={"trace": "0.01"},
        topic_panel_column="Cov",
        topic_panel_value="0.01",
    )
    report = full_tier_report(
        "/x", "t", vitals, debounced_status=content, fallback_status=content, now=10.0, stale_after_seconds=2.0
    )
    assert report.status.severity == Severity.STALE
    assert "LIVE-003" in report.status.codes
    assert report.status.values["trace"] == "0.01"
    assert report.status.topic_panel_column == "Cov"
    assert report.status.topic_panel_value == "0.01"
