"""Unit tests for LiveDataSource/StaticDataSource -- fake manager, no ROS graph."""
from __future__ import annotations

from pathlib import Path

from testudo.core.config import ExcludeMatchType, ExcludeRule, SeverityMode, load_config
from testudo.core.topic_report import TopicReport
from testudo.plugins.base import CheckStatus, Severity
from testudo.tui.data_source import LiveDataSource, StaticDataSource


class _FakeManager:
    def __init__(self) -> None:
        self.tick_calls = 0
        self.reset_calls = 0
        self._reports = [TopicReport("/a", "t", "vitals", CheckStatus(Severity.OK, "l", "m"))]
        self._exclude_rules: list[ExcludeRule] = []

    def tick(self) -> None:
        self.tick_calls += 1

    def reports(self) -> list[TopicReport]:
        return self._reports

    def overall_status(self, mode: SeverityMode) -> CheckStatus:
        return CheckStatus(Severity.OK, "overall", f"mode={mode.value}")

    def reset_stats(self) -> None:
        self.reset_calls += 1

    def exclude_rules(self) -> list[ExcludeRule]:
        return list(self._exclude_rules)

    def add_exclude_rule(self, rule: ExcludeRule) -> int | None:
        if rule in self._exclude_rules:
            return None
        self._exclude_rules.append(rule)
        return 0

    def remove_exclude_rule(self, rule: ExcludeRule) -> bool:
        try:
            self._exclude_rules.remove(rule)
        except ValueError:
            return False
        return True


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


def test_static_data_source_exclude_rules_are_read_only() -> None:
    rules = [ExcludeRule("/velodyne_packets")]
    source = StaticDataSource([], CheckStatus(Severity.OK, "overall", "m"), rules)
    assert source.exclude_rules() == rules
    assert source.add_exclude_rule(ExcludeRule("/x")) is None
    assert source.remove_exclude_rule(rules[0]) is False
    assert source.exclude_rules() == rules  # unaffected


def test_live_data_source_add_exclude_rule_persists_to_the_config_file(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("severity_mode: worst\n")
    manager = _FakeManager()
    source = LiveDataSource(manager, SeverityMode.WORST, config_path=str(path))

    affected = source.add_exclude_rule(ExcludeRule("/velodyne_packets"))

    assert affected == 0
    assert manager.exclude_rules() == [ExcludeRule("/velodyne_packets")]
    assert load_config(path).exclude_topics == [ExcludeRule("/velodyne_packets")]


def test_live_data_source_add_exclude_rule_skips_writing_a_duplicate(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("exclude_topics:\n  - /already_there\n")
    manager = _FakeManager()
    manager._exclude_rules.append(ExcludeRule("/already_there"))
    source = LiveDataSource(manager, SeverityMode.WORST, config_path=str(path))

    assert source.add_exclude_rule(ExcludeRule("/already_there")) is None
    assert path.read_text() == "exclude_topics:\n  - /already_there\n"


def test_live_data_source_remove_exclude_rule_persists_to_the_config_file(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("exclude_topics:\n  - /velodyne_packets\n")
    manager = _FakeManager()
    manager._exclude_rules.append(ExcludeRule("/velodyne_packets"))
    source = LiveDataSource(manager, SeverityMode.WORST, config_path=str(path))

    removed = source.remove_exclude_rule(ExcludeRule("/velodyne_packets"))

    assert removed is True
    assert load_config(path).exclude_topics == []


def test_live_data_source_without_config_path_still_mutates_manager() -> None:
    manager = _FakeManager()
    source = LiveDataSource(manager, SeverityMode.WORST)  # config_path=None
    assert source.add_exclude_rule(ExcludeRule("/x", ExcludeMatchType.LITERAL)) == 0
    assert manager.exclude_rules() == [ExcludeRule("/x")]
