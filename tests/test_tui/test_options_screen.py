"""Pilot-driven tests for the Options menu (`o`) and its Exclude Topics screen.

A `_FakeLiveDataSource` test double stands in for a real `LiveDataSource` +
`SubscriptionManager`: it implements the same `WatchDataSource` protocol
(including `exclude_rules`/`add_exclude_rule`/`remove_exclude_rule`) purely
in memory, so this exercises the screens' wiring without a live ROS graph
or a real config file.
"""
from __future__ import annotations

import asyncio

from testudo.core.config import ExcludeMatchType, ExcludeRule
from testudo.core.topic_report import TopicReport
from testudo.plugins.base import CheckStatus, Severity
from testudo.tui.app import TestudoApp
from testudo.tui.data_source import StaticDataSource, WatchSnapshot
from testudo.tui.options_screen import ExcludeTopicsScreen, OptionsMenuScreen
from testudo.tui.screens import DashboardScreen


class _FakeLiveDataSource:
    def __init__(
        self, initial_rules: list[ExcludeRule] | None = None, reports: list[TopicReport] | None = None
    ) -> None:
        self._rules: list[ExcludeRule] = list(initial_rules or [])
        self._reports: list[TopicReport] = list(reports or [])

    def poll(self) -> WatchSnapshot:
        return WatchSnapshot(reports=self._reports, overall=CheckStatus(Severity.OK, "overall", "0 topic(s)"))

    def reset_stats(self) -> None:
        pass

    def is_live(self) -> bool:
        return True

    def exclude_rules(self) -> list[ExcludeRule]:
        return list(self._rules)

    def add_exclude_rule(self, rule: ExcludeRule) -> int | None:
        if rule in self._rules:
            return None
        self._rules.append(rule)
        return 0

    def remove_exclude_rule(self, rule: ExcludeRule) -> bool:
        try:
            self._rules.remove(rule)
        except ValueError:
            return False
        return True


def _make_app(data_source) -> TestudoApp:
    return TestudoApp(data_source, ros_distro="jazzy", sim_time_active=False)


def run(coro):
    return asyncio.run(coro)


async def _open_exclude_topics(pilot) -> None:
    """`o` opens the Options menu; Enter drills into its one entry, Exclude Topics."""
    await pilot.press("o")
    await pilot.press("enter")


def test_options_menu_opens_on_o_and_lists_exclude_topics() -> None:
    async def body():
        app = _make_app(_FakeLiveDataSource())
        async with app.run_test() as pilot:
            await pilot.press("o")
            assert isinstance(app.screen, OptionsMenuScreen)

            from textual.widgets import OptionList

            option_list = app.screen.query_one(OptionList)
            assert option_list.get_option_at_index(0).prompt == "Exclude Topics"

    run(body())


def test_options_menu_escape_closes_it() -> None:
    async def body():
        app = _make_app(_FakeLiveDataSource())
        async with app.run_test() as pilot:
            await pilot.press("o")
            await pilot.press("escape")
            assert isinstance(app.screen, DashboardScreen)

    run(body())


def test_options_menu_enter_opens_exclude_topics_screen() -> None:
    async def body():
        app = _make_app(_FakeLiveDataSource())
        async with app.run_test() as pilot:
            await _open_exclude_topics(pilot)
            assert isinstance(app.screen, ExcludeTopicsScreen)

    run(body())


def test_escape_from_exclude_topics_goes_back_to_the_options_menu() -> None:
    async def body():
        app = _make_app(_FakeLiveDataSource())
        async with app.run_test() as pilot:
            await _open_exclude_topics(pilot)
            assert isinstance(app.screen, ExcludeTopicsScreen)

            await pilot.press("escape")
            assert isinstance(app.screen, OptionsMenuScreen)

            await pilot.press("escape")
            assert isinstance(app.screen, DashboardScreen)

    run(body())


def test_options_screen_lists_existing_rules() -> None:
    async def body():
        source = _FakeLiveDataSource([ExcludeRule("/velodyne_packets")])
        app = _make_app(source)
        async with app.run_test() as pilot:
            await _open_exclude_topics(pilot)
            from textual.widgets import DataTable

            table = app.screen.query_one("#exclude-table", DataTable)
            assert table.row_count == 1
            assert table.get_row_at(0)[0] == "/velodyne_packets"
            assert table.get_row_at(0)[1] == "literal"

    run(body())


def test_options_screen_adds_a_literal_rule() -> None:
    async def body():
        source = _FakeLiveDataSource()
        app = _make_app(source)
        async with app.run_test() as pilot:
            await _open_exclude_topics(pilot)
            await pilot.press("a")
            for char in "/debug_topic":
                await pilot.press(char)
            await pilot.press("enter")

            assert source.exclude_rules() == [ExcludeRule("/debug_topic")]
            from textual.widgets import DataTable

            table = app.screen.query_one("#exclude-table", DataTable)
            assert table.row_count == 1

    run(body())


def test_options_screen_adds_a_regex_rule_via_prefix() -> None:
    async def body():
        source = _FakeLiveDataSource()
        app = _make_app(source)
        async with app.run_test() as pilot:
            await _open_exclude_topics(pilot)
            await pilot.press("a")
            for char in "regex:_debug$":
                await pilot.press(char)
            await pilot.press("enter")

            assert source.exclude_rules() == [ExcludeRule("_debug$", ExcludeMatchType.REGEX)]

    run(body())


def test_options_screen_adds_a_regex_rule_via_short_prefix() -> None:
    async def body():
        source = _FakeLiveDataSource()
        app = _make_app(source)
        async with app.run_test() as pilot:
            await _open_exclude_topics(pilot)
            await pilot.press("a")
            for char in "re:^/debug_":
                await pilot.press(char)
            await pilot.press("enter")

            assert source.exclude_rules() == [ExcludeRule("^/debug_", ExcludeMatchType.REGEX)]

    run(body())


def test_options_screen_invalid_regex_shows_error_and_does_not_add() -> None:
    async def body():
        source = _FakeLiveDataSource()
        app = _make_app(source)
        async with app.run_test() as pilot:
            await _open_exclude_topics(pilot)
            await pilot.press("a")
            for char in "regex:[":  # invalid regex: unterminated character class
                await pilot.press(char)
            await pilot.press("enter")

            from textual.widgets import Static

            assert source.exclude_rules() == []
            message = str(app.screen.query_one("#options-message", Static).content)
            assert "invalid regex" in message

    run(body())


def test_options_screen_rejects_a_regex_looking_pattern_left_on_literal() -> None:
    """Regression: submitting `_packets$` with no `regex:` prefix used to save silently as an
    unmatchable literal rule -- no error, no match, nothing in Excluded Topics."""

    async def body():
        source = _FakeLiveDataSource()
        app = _make_app(source)
        async with app.run_test() as pilot:
            await _open_exclude_topics(pilot)
            await pilot.press("a")
            for char in "_packets$":
                await pilot.press(char)
            await pilot.press("enter")  # no "regex:" prefix -- literal by default

            assert source.exclude_rules() == []
            from textual.widgets import Static

            message = str(app.screen.query_one("#options-message", Static).content)
            assert "did you mean type: regex" in message

    run(body())


def test_options_screen_preview_shows_zero_matches_for_a_literal_that_wont_match() -> None:
    """Regression: this is exactly the "/filter" mistake -- a literal pattern that reads like it
    should match a substring, but only matches a topic named that exactly, so it silently
    excludes nothing. The live preview should say so before the rule is ever submitted."""

    async def body():
        reports = [
            TopicReport("/local_costmap/filter_layer", "std_msgs/msg/Empty", "vitals", CheckStatus(Severity.OK, "l", "m")),
        ]
        source = _FakeLiveDataSource(reports=reports)
        app = _make_app(source)
        async with app.run_test() as pilot:
            await _open_exclude_topics(pilot)
            await pilot.press("a")
            for char in "/filter":
                await pilot.press(char)

            from textual.widgets import Static

            preview = str(app.screen.query_one("#options-preview", Static).content)
            assert "0 currently-visible" in preview

    run(body())


def test_options_screen_preview_shows_matches_once_using_the_regex_prefix() -> None:
    async def body():
        reports = [
            TopicReport("/local_costmap/filter_layer", "std_msgs/msg/Empty", "vitals", CheckStatus(Severity.OK, "l", "m")),
            TopicReport("/odom", "nav_msgs/msg/Odometry", "vitals", CheckStatus(Severity.OK, "l", "m")),
        ]
        source = _FakeLiveDataSource(reports=reports)
        app = _make_app(source)
        async with app.run_test() as pilot:
            await _open_exclude_topics(pilot)
            await pilot.press("a")
            for char in "regex:filter":
                await pilot.press(char)

            from textual.widgets import Static

            preview = str(app.screen.query_one("#options-preview", Static).content)
            assert "matches 1 currently-visible topic" in preview
            assert "/local_costmap/filter_layer" in preview

    run(body())


def test_options_screen_preview_clears_after_a_successful_add() -> None:
    async def body():
        source = _FakeLiveDataSource()
        app = _make_app(source)
        async with app.run_test() as pilot:
            await _open_exclude_topics(pilot)
            await pilot.press("a")
            for char in "/debug_topic":
                await pilot.press(char)
            await pilot.press("enter")

            from textual.widgets import Static

            preview = str(app.screen.query_one("#options-preview", Static).content)
            assert preview == ""

    run(body())


def test_options_screen_can_delete_a_rule_immediately_after_adding_it() -> None:
    """Regression: submitting the pattern Input never moved focus off it, so the very next
    keystroke -- 'd', meant to delete the row just added -- got typed into the (now empty)
    Input instead of triggering the table's delete binding, forcing a close/reopen of the
    whole screen to get focus back onto the table before delete would work at all."""

    async def body():
        source = _FakeLiveDataSource()
        app = _make_app(source)
        async with app.run_test() as pilot:
            await _open_exclude_topics(pilot)
            await pilot.press("a")
            for char in "/debug_topic":
                await pilot.press(char)
            await pilot.press("enter")
            assert source.exclude_rules() == [ExcludeRule("/debug_topic")]

            await pilot.press("d")  # no closing/reopening the screen in between

            assert source.exclude_rules() == []

    run(body())


def test_options_screen_deletes_a_rule() -> None:
    async def body():
        source = _FakeLiveDataSource([ExcludeRule("/velodyne_packets")])
        app = _make_app(source)
        async with app.run_test() as pilot:
            await _open_exclude_topics(pilot)
            await pilot.press("d")

            assert source.exclude_rules() == []
            from textual.widgets import DataTable

            table = app.screen.query_one("#exclude-table", DataTable)
            assert table.row_count == 0

    run(body())


def test_options_screen_is_read_only_for_a_replay_source() -> None:
    async def body():
        rules = [ExcludeRule("/velodyne_packets")]
        overall = CheckStatus(Severity.OK, "overall", "0 topic(s)")
        source = StaticDataSource([], overall, rules)
        app = _make_app(source)
        async with app.run_test() as pilot:
            await _open_exclude_topics(pilot)
            await pilot.press("a")
            for char in "/new_topic":
                await pilot.press(char)
            await pilot.press("enter")

            # Nothing changed: StaticDataSource.add_exclude_rule is a no-op.
            assert source.exclude_rules() == rules

    run(body())
