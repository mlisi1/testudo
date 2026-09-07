"""Unit tests for LiveDataSource/StaticDataSource -- fake manager, no ROS graph."""
from __future__ import annotations

from testudo.core.config import SeverityMode
from testudo.core.topic_report import TopicReport
from testudo.plugins.base import CheckStatus, Severity
from testudo.tui.data_source import LiveDataSource, StaticDataSource


class _FakeManager:
    def __init__(self) -> None:
        self.tick_calls = 0
        self.reset_calls = 0
        self._reports = [TopicReport("/a", "t", "vitals", CheckStatus(Severity.OK, "l", "m"))]

    def tick(self) -> None:
        self.tick_calls += 1

    def reports(self) -> list[TopicReport]:
        return self._reports

    def overall_status(self, mode: SeverityMode) -> CheckStatus:
        return CheckStatus(Severity.OK, "overall", f"mode={mode.value}")

    def reset_stats(self) -> None:
        self.reset_calls += 1


def test_live_data_source_ticks_manager_on_each_poll() -> None:
    manager = _FakeManager()
    source = LiveDataSource(manager, SeverityMode.WORST)

    snapshot = source.poll()

    assert manager.tick_calls == 1
    assert snapshot.reports == manager.reports()
    assert snapshot.overall.message == "mode=worst"
    assert source.is_live() is True


def test_live_data_source_reset_stats_delegates_to_manager() -> None:
    manager = _FakeManager()
    source = LiveDataSource(manager, SeverityMode.WORST)
    source.reset_stats()
    assert manager.reset_calls == 1


def test_static_data_source_returns_same_snapshot_every_poll() -> None:
    reports = [TopicReport("/a", "t", "full", CheckStatus(Severity.WARN, "l", "m"))]
    overall = CheckStatus(Severity.WARN, "overall", "1 topic(s)")
    source = StaticDataSource(reports, overall)

    first = source.poll()
    second = source.poll()

    assert first is second
    assert first.reports == reports
    assert source.is_live() is False


def test_static_data_source_reset_stats_is_a_safe_no_op() -> None:
    source = StaticDataSource([], CheckStatus(Severity.OK, "overall", "m"))
    source.reset_stats()  # must not raise
