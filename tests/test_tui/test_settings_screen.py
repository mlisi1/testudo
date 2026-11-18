"""Pilot tests for Options > Checks and Options > Display, and the help overlay's bold keys."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from textual.content import Content
from textual.widgets import OptionList

from testudo.core.config import SeverityMode
from testudo.core.config_loader import load_config
from testudo.core.live_settings import CHECK_SETTINGS
from testudo.core.topic_report import TopicReport
from testudo.plugins.base import CheckStatus, Severity
from testudo.tui.app import TestudoApp
from testudo.tui.data_source import LiveDataSource, StaticDataSource
from testudo.tui.display_prefs import BY_NAME, DISPLAY_SETTINGS, DisplayPrefs, load_display_prefs
from testudo.tui.keybinds import HELP_TEXT
from testudo.tui.settings_screen import SettingsScreen, format_value, next_choice
from testudo.tui.widgets.category_summary import CategorySummaryTable
from testudo.tui.widgets.topic_panel import TopicDetailTable


def _reports() -> list[TopicReport]:
    return [
        TopicReport("/bad", "sensor_msgs/msg/Imu", "full", CheckStatus(Severity.WARN, "imu", "m", codes={"IMU-001": "x"})),
        TopicReport("/good", "sensor_msgs/msg/Imu", "full", CheckStatus(Severity.OK, "imu", "m")),
        TopicReport("/proc", "node", "node", CheckStatus(Severity.WARN, "node", "m", codes={"NODE-001": "x"})),
    ]


class _Manager:
    def __init__(self) -> None:
        self._max_check_rate_hz = 10.0
        self._hysteresis_required_consecutive = 3
        self._full_tier: dict = {}

    def tick(self) -> None:
        pass

    def reports(self) -> list[TopicReport]:
        return _reports()

    def overall_status(self, mode: SeverityMode) -> CheckStatus:
        return CheckStatus(Severity.OK, "overall", mode.value)


def _app(tmp_path: Path, prefs: DisplayPrefs | None = None) -> tuple[TestudoApp, LiveDataSource]:
    source = LiveDataSource(_Manager(), SeverityMode.WORST, config_path=str(tmp_path / "config.yaml"))
    return TestudoApp(source, ros_distro="jazzy", display_prefs=prefs, display_prefs_path=tmp_path / "display.yaml"), source


def _prompts(app: TestudoApp) -> list[str]:
    options = app.screen.query_one(OptionList)
    return [str(options.get_option_at_index(index).prompt) for index in range(options.option_count)]


def _topics(app: TestudoApp) -> list[str]:
    return list(app.screen.query_one(TopicDetailTable)._topic_order)


def test_the_menu_lists_every_section_live(tmp_path: Path) -> None:
    async def body() -> None:
        app = _app(tmp_path)[0]
        async with app.run_test() as pilot:
            await pilot.press("o")
            assert _prompts(app) == ["Exclude Topics", "Checks", "Display"]

    asyncio.run(body())


def test_a_replay_has_no_checks_to_change() -> None:
    async def body() -> None:
        app = TestudoApp(StaticDataSource([], CheckStatus(Severity.OK, "overall", "")), ros_distro="jazzy")
        async with app.run_test() as pilot:
            await pilot.press("o")
            assert _prompts(app) == ["Exclude Topics", "Display"]

    asyncio.run(body())


def test_a_check_changes_live_and_saves_to_the_robot_config(tmp_path: Path) -> None:
    async def body() -> None:
        app, source = _app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("o", "down", "enter")
            assert isinstance(app.screen, SettingsScreen)
            await pilot.press("enter")
            assert "weighted" in _prompts(app)[0]
            assert source.severity_mode is SeverityMode.WEIGHTED
            await pilot.press("down", "space")
            assert "20" in _prompts(app)[1]
            await pilot.press("escape")
            assert not isinstance(app.screen, SettingsScreen)
        config = load_config(tmp_path / "config.yaml")
        assert config.severity_mode is SeverityMode.WEIGHTED and config.max_check_rate_hz == 20.0
        assert not (tmp_path / "display.yaml").exists()

    asyncio.run(body())


def test_a_check_that_needs_a_restart_says_so(tmp_path: Path) -> None:
    async def body() -> None:
        app, source = _app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("o", "down", "enter", "down", "down", "down", "down", "enter")
            assert "next start" in str(app.screen.query_one("#options-message").render())
            assert source.checks.values()["node_profiling.enabled"] is True

    asyncio.run(body())


def test_a_display_preference_goes_to_the_user_file_not_the_robot_config(tmp_path: Path) -> None:
    async def body() -> None:
        app = _app(tmp_path)[0]
        async with app.run_test() as pilot:
            await pilot.press("o", "down", "down", "enter", "down", "enter")
            await pilot.pause()
            assert app.display_prefs.default_sort == BY_NAME
        assert load_display_prefs(tmp_path / "display.yaml").default_sort == BY_NAME
        assert not (tmp_path / "config.yaml").exists()

    asyncio.run(body())


def _help_text(app: TestudoApp, selector: str = "#settings-help") -> str:
    return str(app.screen.query_one(selector).render())


def test_each_setting_explains_itself_beside_the_list(tmp_path: Path) -> None:
    async def body() -> None:
        app = _app(tmp_path)[0]
        async with app.run_test() as pilot:
            await pilot.press("o", "down", "enter")
            await pilot.pause()
            assert "overall verdict" in _help_text(app)
            await pilot.press("down")
            await pilot.pause()
            assert "content is checked" in _help_text(app)
            await pilot.press("enter")
            await pilot.pause()
            assert "content is checked" in _help_text(app)

    asyncio.run(body())


def test_the_menu_explains_each_section_beside_the_list(tmp_path: Path) -> None:
    async def body() -> None:
        app = _app(tmp_path)[0]
        async with app.run_test() as pilot:
            await pilot.press("o")
            await pilot.pause()
            assert "stop watching" in _help_text(app, "#options-menu-help")
            await pilot.press("down", "down")
            await pilot.pause()
            assert "your own preferences" in _help_text(app, "#options-menu-help")

    asyncio.run(body())


@pytest.mark.parametrize("section_downs", [1, 2])
def test_every_description_fits_a_small_terminal(tmp_path: Path, section_downs: int) -> None:
    async def body() -> None:
        app = _app(tmp_path)[0]
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.press("o", *["down"] * section_downs, "enter")
            await pilot.pause()
            for _ in range(app.screen.query_one(OptionList).option_count):
                await pilot.pause()
                panel = app.screen.query_one("#settings-panel").region
                assert panel.y >= 0 and panel.bottom <= 24
                assert app.screen.query_one("#settings-help").region.bottom < panel.bottom
                assert app.screen.query_one("#options-message").region.bottom < panel.bottom
                await pilot.press("down")

    asyncio.run(body())


def test_every_setting_has_help() -> None:
    assert all(item.help for item in (*CHECK_SETTINGS, *DISPLAY_SETTINGS))


def test_a_refresh_rate_above_the_data_rate_says_it_cannot_draw_faster(tmp_path: Path) -> None:
    async def body() -> None:
        app = _app(tmp_path, DisplayPrefs(refresh_hz=10.0))[0]
        async with app.run_test() as pilot:
            await pilot.press("o", "down", "down", "enter")
            await pilot.pause()
            assert "can't draw faster" in _help_text(app)

    asyncio.run(body())


def test_the_whole_saved_path_is_shown(tmp_path: Path) -> None:
    async def body() -> None:
        deep = tmp_path / ("long_directory_name_" * 8)
        app = _app(deep)[0]
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.press("o", "down", "down", "enter", "enter")
            await pilot.pause()
            assert app.screen.query_one("#options-message").region.height >= 2

    asyncio.run(body())


def test_the_default_sort_applies_at_startup(tmp_path: Path) -> None:
    async def body() -> None:
        app = _app(tmp_path, DisplayPrefs(default_sort=BY_NAME))[0]
        async with app.run_test():
            assert not app.screen.query_one(CategorySummaryTable).sort_by_severity
            assert not app.screen.query_one(TopicDetailTable).sort_by_severity

    asyncio.run(body())


def test_hiding_ok_topics_leaves_the_problems(tmp_path: Path) -> None:
    async def body() -> None:
        app = _app(tmp_path)[0]
        async with app.run_test() as pilot:
            app.screen.query_one(CategorySummaryTable).select_category("IMU")
            await pilot.pause()
            assert set(_topics(app)) == {"/bad", "/good"}
            app.change_display_pref("hide_ok_topics", True)
            await pilot.pause()
            assert _topics(app) == ["/bad"]

    asyncio.run(body())


def test_switching_nodes_off_takes_them_out_of_the_panes_and_the_problem_jump(tmp_path: Path) -> None:
    async def body() -> None:
        app = _app(tmp_path, DisplayPrefs(show_nodes=False))[0]
        async with app.run_test() as pilot:
            categories = app.screen.query_one(CategorySummaryTable)
            assert "Nodes" not in categories._category_order
            await pilot.press("n", "n")
            await pilot.pause()
            assert app.screen.query_one(TopicDetailTable).selected_topic == "/bad"

    asyncio.run(body())


def test_the_display_refresh_rate_limits_drawing_but_not_polling(tmp_path: Path) -> None:
    async def body() -> None:
        app, _source = _app(tmp_path, DisplayPrefs(refresh_hz=1.0))
        snapshots = []
        app._on_snapshot = snapshots.append
        async with app.run_test():
            draws = []
            app._notify_dashboard = lambda: draws.append(1)
            app._last_draw_monotonic = 0.0
            for _ in range(3):
                app._poll()
            assert len(snapshots) >= 3 and len(draws) == 1
            app.refresh_now()
            assert len(draws) == 2

    asyncio.run(body())


@pytest.mark.parametrize(
    ("choices", "current", "expected"),
    [
        ((1, 2, 3), 2, 3),
        ((1, 2, 3), 3, 1),
        ((5.0, 10.0, 20.0), 12.0, 20.0),
        ((5.0, 10.0), 99.0, 5.0),
        ((True, False), True, False),
        (("a", "b"), "z", "a"),
    ],
)
def test_next_choice(choices: tuple, current: object, expected: object) -> None:
    assert next_choice(choices, current) == expected


def test_a_count_of_one_is_not_shown_as_on() -> None:
    assert format_value(1) == "1" and format_value(True) == "on" and format_value(10.0) == "10"


def test_every_key_in_the_help_is_bold() -> None:
    content = Content.from_markup(HELP_TEXT)
    assert "[ / ]" in content.plain and "{ / }" in content.plain
    bold = {content.plain[span.start : span.end] for span in content.spans if "b" in str(span.style).split()}
    assert {"q", "[ / ]", "{ / }", "j / k / arrows", "w", "esc", "?"} <= bold
