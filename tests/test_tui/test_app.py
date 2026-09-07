"""Pilot-driven integration tests for the Textual app -- headless, no real terminal.

Runs the whole navigation flow (summary -> category -> topic detail -> back,
filter, sort, help, pause, reset) against a StaticDataSource built from
synthetic TopicReports, so this needs no ROS graph either.
"""
from __future__ import annotations

import asyncio

from testudo.core.topic_report import TopicReport
from testudo.plugins.base import CheckStatus, Severity
from testudo.tui.app import TestudoApp
from testudo.tui.data_source import StaticDataSource
from testudo.tui.screens import CategoryScreen, HelpScreen, SummaryScreen, TopicDetailScreen


def _reports() -> list[TopicReport]:
    return [
        TopicReport("/odom", "nav_msgs/msg/Odometry", "full", CheckStatus(Severity.ERROR, "odometry", "covariance too high")),
        TopicReport("/scan", "sensor_msgs/msg/LaserScan", "full", CheckStatus(Severity.OK, "sensor", "nominal")),
        TopicReport("/battery", "sensor_msgs/msg/BatteryState", "vitals", CheckStatus(Severity.OK, "liveness", "alive")),
    ]


def _make_app() -> TestudoApp:
    overall = CheckStatus(Severity.ERROR, "overall", "3 topic(s): OK=2, ERROR=1")
    source = StaticDataSource(_reports(), overall)
    return TestudoApp(source, ros_distro="jazzy", sim_time_active=False)


def run(coro):
    return asyncio.run(coro)


def test_app_starts_on_summary_screen_with_categories() -> None:
    async def body():
        app = _make_app()
        async with app.run_test() as pilot:
            assert isinstance(app.screen, SummaryScreen)
            table = app.screen.query_one("#category-table")
            # 3 reports across 3 categories: Odometry, LaserScan, Other Topics.
            assert table.row_count == 3

    run(body())


def test_drill_down_into_category_then_topic_detail_then_back() -> None:
    async def body():
        app = _make_app()
        async with app.run_test() as pilot:
            # Sort by severity (default) puts /odom's "Odometry" category on top (ERROR, worst).
            await pilot.press("enter")
            assert isinstance(app.screen, CategoryScreen)
            assert app.screen._category == "Odometry"

            await pilot.press("enter")
            assert isinstance(app.screen, TopicDetailScreen)
            rendered = app.screen.query_one("#topic-detail").content
            assert "/odom" in str(rendered)
            assert "covariance too high" in str(rendered)

            await pilot.press("escape")
            assert isinstance(app.screen, CategoryScreen)

            await pilot.press("escape")
            assert isinstance(app.screen, SummaryScreen)

    run(body())


def test_help_screen_opens_and_closes_on_any_key() -> None:
    async def body():
        app = _make_app()
        async with app.run_test() as pilot:
            await pilot.press("question_mark")
            assert isinstance(app.screen, HelpScreen)
            await pilot.press("x")
            assert isinstance(app.screen, SummaryScreen)

    run(body())


def test_filter_narrows_category_table() -> None:
    async def body():
        app = _make_app()
        async with app.run_test() as pilot:
            await pilot.press("slash")
            for char in "odom":
                await pilot.press(char)
            await pilot.press("enter")
            table = app.screen.query_one("#category-table")
            assert table.row_count == 1

    run(body())


def test_escape_while_filtering_clears_filter_instead_of_going_back() -> None:
    async def body():
        app = _make_app()
        async with app.run_test() as pilot:
            await pilot.press("slash")
            for char in "odom":
                await pilot.press(char)
            table = app.screen.query_one("#category-table")
            assert table.row_count == 1

            await pilot.press("escape")
            assert isinstance(app.screen, SummaryScreen)  # didn't navigate away
            assert table.row_count == 3  # filter cleared

    run(body())


def test_sort_toggle_changes_row_order() -> None:
    async def body():
        app = _make_app()
        async with app.run_test() as pilot:
            table = app.screen.query_one("#category-table")
            severity_order_first = table.get_row_at(0)[0]
            await pilot.press("s")
            name_order_first = table.get_row_at(0)[0]
            # Alphabetical first ("LaserScan") differs from severity-first ("Odometry", the ERROR one).
            assert severity_order_first != name_order_first

    run(body())


def test_pause_is_a_no_op_for_a_static_replay_source() -> None:
    async def body():
        app = _make_app()
        async with app.run_test() as pilot:
            assert app.paused is False
            await pilot.press("p")
            assert app.paused is False  # StaticDataSource.is_live() is False

    run(body())


def test_reset_stats_does_not_crash_for_static_source() -> None:
    async def body():
        app = _make_app()
        async with app.run_test() as pilot:
            await pilot.press("r")  # must not raise
            assert isinstance(app.screen, SummaryScreen)

    run(body())


def test_cursor_position_survives_a_data_refresh() -> None:
    """A poll (e.g. a live tick) shouldn't reset the user's navigation position."""

    async def body():
        app = _make_app()
        async with app.run_test() as pilot:
            await pilot.press("enter")  # into "Odometry" (only 1 topic, but exercise the mechanism)
            table = app.screen.query_one("#topic-table")
            assert table.selected_topic == "/odom"

            app.screen.refresh_table()  # simulate a poll-driven refresh with unchanged data
            assert table.selected_topic == "/odom"
            assert table.cursor_row == 0

    run(body())
