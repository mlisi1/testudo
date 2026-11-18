"""Unit tests for the per-user display preferences file."""
from __future__ import annotations

from pathlib import Path

import pytest

from testudo.core.topic_report import TopicReport
from testudo.plugins.base import CheckStatus, Severity
from testudo.tui.display_prefs import (
    BY_NAME,
    DisplayPrefs,
    default_display_prefs_path,
    load_display_prefs,
    save_display_pref,
    shows_topic,
    visible_reports,
)


def _report(tier: str = "full", severity: int = Severity.OK) -> TopicReport:
    return TopicReport("/a", "t", tier, CheckStatus(severity, "l", "m"))


def test_a_missing_file_means_the_defaults(tmp_path: Path) -> None:
    assert load_display_prefs(tmp_path / "display.yaml") == DisplayPrefs()


def test_the_file_sits_beside_the_default_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert default_display_prefs_path() == tmp_path / "testudo" / "display.yaml"


def test_saved_preferences_load_back_and_keep_the_file_s_comments(tmp_path: Path) -> None:
    path = tmp_path / "display.yaml"
    path.write_text("# mine\nrefresh_hz: 2  # slow link\n")
    save_display_pref(path, "refresh_hz", 10.0)
    save_display_pref(path, "default_sort", BY_NAME)
    assert path.read_text().startswith("# mine\nrefresh_hz: 10.0  # slow link\n")
    assert load_display_prefs(path) == DisplayPrefs(refresh_hz=10.0, default_sort=BY_NAME)


def test_saving_creates_the_file_and_its_directory(tmp_path: Path) -> None:
    path = tmp_path / "testudo" / "display.yaml"
    save_display_pref(path, "show_nodes", False)
    assert load_display_prefs(path).show_nodes is False


@pytest.mark.parametrize("text", ["a: [1, 2\n", "- a\n", "\x00"])
def test_a_broken_file_never_stops_the_tui(tmp_path: Path, text: str) -> None:
    path = tmp_path / "display.yaml"
    path.write_text(text)
    assert load_display_prefs(path) == DisplayPrefs()


def test_one_bad_value_keeps_the_good_ones(tmp_path: Path) -> None:
    path = tmp_path / "display.yaml"
    path.write_text("refresh_hz: -1\ndefault_sort: sideways\nhide_ok_topics: yes\nshow_nodes: 3\nfuture_key: 1\n")
    assert load_display_prefs(path) == DisplayPrefs(hide_ok_topics=True)


def test_hiding_ok_topics_keeps_problems_and_rows_never_judged() -> None:
    prefs = DisplayPrefs(hide_ok_topics=True)
    assert not shows_topic(_report(), prefs)
    assert shows_topic(_report(severity=Severity.WARN), prefs)
    assert shows_topic(_report("excluded"), prefs)
    assert shows_topic(_report("node"), prefs)
    assert shows_topic(_report(), DisplayPrefs())


def test_switching_nodes_off_removes_only_node_rows() -> None:
    reports = [_report(), _report("node")]
    assert visible_reports(reports, DisplayPrefs(show_nodes=False)) == [reports[0]]
    assert visible_reports(reports, DisplayPrefs()) == reports
