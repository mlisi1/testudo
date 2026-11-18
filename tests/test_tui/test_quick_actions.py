"""Pilot tests for the quick actions line: what it offers, and what each key changes and saves."""
from __future__ import annotations

import asyncio
import re
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from testudo.core.code_overrides import apply_code_overrides
from testudo.core.config import ExcludeRule, SeverityMode, TopicConfig
from testudo.core.config_loader import load_config
from testudo.core.topic_report import TopicReport
from testudo.plugins.base import CheckStatus, CodeMetric, Severity, SeverityLevels
from testudo.plugins.builtin import actions, bt_log, costmap, gnss, image_stream, imu, localization, point_stream, velocity_command
from testudo.tui.app import TestudoApp
from testudo.tui.data_source import LiveDataSource, StaticDataSource
from testudo.tui.keybinds import HELP_TEXT
from testudo.tui.quick_actions import CodeChoice, QuickActionBar, line_markup
from testudo.tui.widgets.topic_panel import TopicDetailTable

_GRID = "nav_msgs/msg/OccupancyGrid"


class _Manager:
    """The part of a SubscriptionManager the TUI touches, holding one topic with two codes."""

    def __init__(self) -> None:
        self._topic_config_by_name: dict[str, TopicConfig] = {}
        self._action_config_by_status_topic: dict = {}
        self._msg_type_by_topic = {"/grid": _GRID}
        plugin = SimpleNamespace(levels={"lethal_ratio": SeverityLevels(warn=0.6, error=0.9)})
        self._full_tier = {"/grid": SimpleNamespace(plugin=plugin)}
        self.rules: list[ExcludeRule] = []

    def tick(self) -> None:
        pass

    def reports(self) -> list[TopicReport]:
        status = CheckStatus(
            Severity.ERROR,
            "costmap",
            "bad",
            codes={"MAP-001": "empty", "MAP-002": "lethal"},
            code_severities={"MAP-001": Severity.ERROR, "MAP-002": Severity.WARN},
            code_metrics={"MAP-002": CodeMetric("lethal_ratio", 0.7)},
        )
        config = self._topic_config_by_name.get("/grid")
        return [TopicReport("/grid", _GRID, "full", apply_code_overrides(status, config.codes if config else {}))]

    def overall_status(self, mode: SeverityMode) -> CheckStatus:
        return CheckStatus(Severity.OK, "overall", "")

    def exclude_rules(self) -> list[ExcludeRule]:
        return self.rules

    def add_exclude_rule(self, rule: ExcludeRule) -> int:
        self.rules.append(rule)
        return 1


def _app(tmp_path: Path) -> tuple[TestudoApp, _Manager, Path]:
    manager = _Manager()
    path = tmp_path / "config.yaml"
    source = LiveDataSource(manager, SeverityMode.WORST, config_path=str(path))
    return TestudoApp(source, ros_distro="jazzy", poll_rate_hz=0.1), manager, path


def _bar_text(app: TestudoApp) -> str:
    return str(app.screen.query_one(QuickActionBar).render())


def _drive(tmp_path: Path, *keys: str) -> tuple[_Manager, Path, str]:
    """Press `a` and then `keys`; returns what the line said right after `a`."""

    async def body() -> tuple[_Manager, Path, str]:
        app, manager, path = _app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("right", "a")
            text = _bar_text(app)
            await pilot.press(*keys)
            await pilot.pause()
            bar = app.screen.query_one(QuickActionBar)
            assert not bar.display
            assert app.screen.query_one("#hint").display
            assert isinstance(app.focused, TopicDetailTable)
        return manager, path, text

    return asyncio.run(body())


def test_the_line_opens_on_the_worst_code_in_place_of_the_hint(tmp_path: Path) -> None:
    async def body() -> None:
        app, _manager, _path = _app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("right", "a")
            assert app.screen.query_one(QuickActionBar).display
            assert not app.screen.query_one("#hint").display
            assert "MAP-001" in _bar_text(app) and "downgrade" in _bar_text(app)
            await pilot.press("c")
            assert "MAP-002" in _bar_text(app) and "warn at 0.7" in _bar_text(app)

    asyncio.run(body())


def test_escape_changes_nothing(tmp_path: Path) -> None:
    manager, path, _text = _drive(tmp_path, "escape")
    assert manager._topic_config_by_name == {} and not path.exists()


def test_m_mutes_the_code_and_saves_it(tmp_path: Path) -> None:
    manager, path, _text = _drive(tmp_path, "m")
    assert manager._topic_config_by_name["/grid"].codes == {"MAP-001": Severity.OK}
    assert load_config(path).topics[_GRID][0].codes == {"MAP-001": Severity.OK}


def test_w_downgrades_the_code_and_saves_it(tmp_path: Path) -> None:
    _manager, path, _text = _drive(tmp_path, "w")
    assert load_config(path).topics[_GRID][0].codes == {"MAP-001": Severity.WARN}


def test_l_sets_warn_from_the_current_value_and_keeps_error(tmp_path: Path) -> None:
    manager, path, _text = _drive(tmp_path, "c", "l")
    assert manager._full_tier["/grid"].plugin.levels["lethal_ratio"] == SeverityLevels(warn=0.7, error=0.9)
    override = load_config(path).topics[_GRID][0].levels["lethal_ratio"]
    assert override.warn == 0.7 and "error" not in override.given


def test_x_excludes_the_topic(tmp_path: Path) -> None:
    manager, path, _text = _drive(tmp_path, "x")
    assert manager.rules == [ExcludeRule("/grid")]
    assert load_config(path).exclude_topics == [ExcludeRule("/grid")]


def test_a_muted_code_can_be_restored(tmp_path: Path) -> None:
    async def body() -> None:
        app, manager, path = _app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("right", "a", "m")
            await pilot.pause()
            await pilot.press("a")
            assert "MAP-002" in _bar_text(app)
            await pilot.press("c")
            assert "MAP-001" in _bar_text(app) and "restore" in _bar_text(app)
            await pilot.press("r")
            await pilot.pause()
        assert manager._topic_config_by_name["/grid"].codes == {}
        assert load_config(path).topics[_GRID][0].codes == {}

    asyncio.run(body())


def test_a_failed_save_still_applies_for_the_session(tmp_path: Path) -> None:
    async def body() -> None:
        app, manager, path = _app(tmp_path)
        path.write_text("topics: [1, 2\n")
        async with app.run_test() as pilot:
            await pilot.press("right", "a", "m")
            await pilot.pause()
            assert any("session only" in note.message for note in app._notifications)
        assert manager._topic_config_by_name["/grid"].codes == {"MAP-001": Severity.OK}
        assert path.read_text() == "topics: [1, 2\n"

    asyncio.run(body())


def test_a_replay_offers_nothing(tmp_path: Path) -> None:
    async def body() -> None:
        report = TopicReport("/grid", _GRID, "full", CheckStatus(Severity.WARN, "costmap", "bad"))
        app = TestudoApp(StaticDataSource([report], CheckStatus(Severity.WARN, "overall", "")), ros_distro="jazzy")
        async with app.run_test() as pilot:
            await pilot.press("right", "a")
            assert not app.screen.query_one(QuickActionBar).display

    asyncio.run(body())


def test_a_value_past_error_offers_no_warn_level() -> None:
    report = TopicReport("/grid", _GRID, "full", CheckStatus(Severity.ERROR, "costmap", "bad"))
    choice = CodeChoice("MAP-002", Severity.ERROR, False, CodeMetric("lethal_ratio", 0.95), None)
    text = line_markup(report, [choice], 0)
    assert "warn at" not in text and "past its error level" in text


def test_a_derived_row_cannot_be_excluded() -> None:
    report = replace(TopicReport("map → odom", "t", "derived", CheckStatus(Severity.WARN, "l", "m")), source_topic="/tf")
    assert "exclude" not in line_markup(report, [], 0)


@pytest.mark.parametrize(
    "module", [actions, bt_log, costmap, gnss, image_stream, imu, localization, point_stream, velocity_command]
)
def test_every_code_metric_names_a_level_the_plugin_reads(module) -> None:
    source = Path(module.__file__).read_text()
    named = set(re.findall(r'CodeMetric\("(\w+)"', source))
    assert named and named <= set(re.findall(r'(?:levels\.get|_levels_for)\("(\w+)"\)', source))


def test_the_help_lists_every_quick_action_key() -> None:
    keys = {binding.key for binding in QuickActionBar.BINDINGS}
    section = HELP_TEXT[HELP_TEXT.index("[b]a[/b]") : HELP_TEXT.index("[b]?[/b]")]
    assert keys == {"w", "m", "l", "r", "c", "x", "escape"}
    for key in keys:
        assert f"[b]{'esc' if key == 'escape' else key}[/b]" in section, key
