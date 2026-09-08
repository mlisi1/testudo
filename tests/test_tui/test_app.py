"""Pilot-driven integration tests for the Textual dashboard -- headless, no
real terminal.

Categories, topics, and detail are all visible at once and update from
cursor movement (RowHighlighted), not from pressing Enter -- these tests
exercise exactly that live-update wiring, against a StaticDataSource built
from synthetic TopicReports, so no ROS graph is needed either.
"""
from __future__ import annotations

import asyncio

from testudo.core.topic_report import TopicReport
from testudo.plugins.base import CheckStatus, Severity
from testudo.tui.app import TestudoApp
from testudo.tui.data_source import StaticDataSource
from testudo.tui.screens import DashboardScreen, HelpScreen
from testudo.tui.widgets.category_summary import CategorySummaryTable
from testudo.tui.widgets.topic_panel import TopicDetailTable


def _reports() -> list[TopicReport]:
    return [
        TopicReport(
            "/odom",
            "nav_msgs/msg/Odometry",
            "full",
            CheckStatus(
                Severity.ERROR,
                "odometry",
                "covariance too high",
                topic_panel_column="Cov",
                topic_panel_value="[bold red]0.6[/bold red]",
            ),
        ),
        TopicReport("/scan", "sensor_msgs/msg/LaserScan", "full", CheckStatus(Severity.OK, "sensor", "nominal")),
        TopicReport("/battery", "sensor_msgs/msg/BatteryState", "vitals", CheckStatus(Severity.OK, "liveness", "alive")),
    ]


def _make_app() -> TestudoApp:
    overall = CheckStatus(Severity.ERROR, "overall", "3 topic(s): OK=2, ERROR=1")
    source = StaticDataSource(_reports(), overall)
    return TestudoApp(source, ros_distro="jazzy", sim_time_active=False)


def run(coro):
    return asyncio.run(coro)


def test_app_starts_with_all_three_panes_populated() -> None:
    async def body():
        app = _make_app()
        async with app.run_test():
            assert isinstance(app.screen, DashboardScreen)
            categories = app.screen.query_one(CategorySummaryTable)
            topics = app.screen.query_one(TopicDetailTable)
            # 3 reports across 3 categories: Odometry, LaserScan, Other Topics.
            assert categories.row_count == 3
            # Severity sort puts "Odometry" (ERROR, worst) on top by default,
            # so its 1 topic should already be showing in the topics pane
            # without pressing anything.
            assert topics.row_count == 1
            assert app.screen.focused is categories

            # status.message ("covariance too high") is deliberately not
            # shown -- error reporting is getting its own treatment later.
            detail = app.screen.query_one("#detail-pane").content
            assert "/odom" in str(detail)
            assert "nav_msgs/msg/Odometry" in str(detail)

    run(body())


def test_moving_category_cursor_live_updates_topics_and_detail() -> None:
    async def body():
        app = _make_app()
        async with app.run_test() as pilot:
            await pilot.press("j")  # move off "Odometry" onto the next category
            topics = app.screen.query_one(TopicDetailTable)
            categories = app.screen.query_one(CategorySummaryTable)
            new_category = categories.selected_category
            assert new_category != "Odometry"
            assert all(t != "/odom" for t in topics._topic_order)

            detail = str(app.screen.query_one("#detail-pane").content)
            assert "/odom" not in detail

    run(body())


def test_enter_switches_focus_between_panes() -> None:
    async def body():
        app = _make_app()
        async with app.run_test() as pilot:
            categories = app.screen.query_one(CategorySummaryTable)
            topics = app.screen.query_one(TopicDetailTable)
            assert app.screen.focused is categories

            await pilot.press("enter")
            assert app.screen.focused is topics

            await pilot.press("enter")
            assert app.screen.focused is categories

    run(body())


def test_moving_topic_cursor_updates_detail_pane() -> None:
    async def body():
        app = _make_app()
        async with app.run_test() as pilot:
            await pilot.press("enter")  # focus topics (only 1 row: /odom)
            detail = str(app.screen.query_one("#detail-pane").content)
            assert "/odom" in detail

    run(body())


def test_help_screen_opens_and_closes_on_any_key() -> None:
    async def body():
        app = _make_app()
        async with app.run_test() as pilot:
            await pilot.press("question_mark")
            assert isinstance(app.screen, HelpScreen)
            await pilot.press("x")
            assert isinstance(app.screen, DashboardScreen)

    run(body())


def test_filter_narrows_the_focused_table() -> None:
    async def body():
        app = _make_app()
        async with app.run_test() as pilot:
            await pilot.press("slash")
            for char in "laser":
                await pilot.press(char)
            await pilot.press("enter")
            categories = app.screen.query_one(CategorySummaryTable)
            assert categories.row_count == 1

    run(body())


def test_escape_while_filtering_clears_filter_instead_of_moving_focus() -> None:
    async def body():
        app = _make_app()
        async with app.run_test() as pilot:
            await pilot.press("slash")
            for char in "laser":
                await pilot.press(char)
            categories = app.screen.query_one(CategorySummaryTable)
            assert categories.row_count == 1

            await pilot.press("escape")
            assert categories.row_count == 3  # filter cleared

    run(body())


def test_escape_without_filter_focuses_categories() -> None:
    async def body():
        app = _make_app()
        async with app.run_test() as pilot:
            categories = app.screen.query_one(CategorySummaryTable)
            topics = app.screen.query_one(TopicDetailTable)
            topics.focus()
            await pilot.pause()
            assert app.screen.focused is topics

            await pilot.press("escape")
            assert app.screen.focused is categories

    run(body())


def test_sort_toggle_changes_focused_table_row_order() -> None:
    async def body():
        app = _make_app()
        async with app.run_test() as pilot:
            categories = app.screen.query_one(CategorySummaryTable)
            severity_order_first = categories.get_row_at(0)[0]
            await pilot.press("s")
            name_order_first = categories.get_row_at(0)[0]
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
            assert isinstance(app.screen, DashboardScreen)

    run(body())


def test_cursor_position_survives_a_data_refresh() -> None:
    """A poll (e.g. a live tick) shouldn't reset the user's navigation position."""

    async def body():
        app = _make_app()
        async with app.run_test():
            categories = app.screen.query_one(CategorySummaryTable)
            assert categories.selected_category == "Odometry"

            app.screen.refresh_categories()  # simulate a poll-driven refresh with unchanged data
            assert categories.selected_category == "Odometry"
            assert categories.cursor_row == 0

    run(body())


def test_scroll_position_survives_a_data_refresh_with_many_rows() -> None:
    """Regression: DataTable.clear() resets scroll_y to 0, so refreshing a
    scrolled-down table every poll used to flicker back to the top and jump
    back down again on every tick.
    """

    def many_reports() -> list[TopicReport]:
        return [
            TopicReport(f"/topic_{i}", "std_msgs/msg/Empty", "vitals", CheckStatus(Severity.OK, "l", f"m{i}"))
            for i in range(40)
        ]

    async def body():
        overall = CheckStatus(Severity.OK, "overall", "40 topic(s)")
        source = StaticDataSource(many_reports(), overall)
        app = TestudoApp(source, ros_distro="jazzy", sim_time_active=False)
        async with app.run_test(size=(80, 15)) as pilot:
            # Everything lands in "Other Topics" (vitals tier) -- only 1
            # category, but its topics table is genuinely long/scrollable.
            topics = app.screen.query_one(TopicDetailTable)
            assert topics.row_count == 40

            topics.scroll_y = 10
            await pilot.pause()
            scroll_before = topics.scroll_y
            assert scroll_before > 0

            app.screen.refresh_categories()  # simulate a poll-driven refresh, unchanged data
            await pilot.pause()
            assert topics.scroll_y == scroll_before

    run(body())


def test_other_topics_category_always_sorts_last() -> None:
    async def body():
        reports = [
            TopicReport("/battery", "sensor_msgs/msg/BatteryState", "vitals", CheckStatus(Severity.ERROR, "l", "dead")),
            TopicReport("/odom", "nav_msgs/msg/Odometry", "full", CheckStatus(Severity.OK, "odometry", "nominal")),
            TopicReport("/scan", "sensor_msgs/msg/LaserScan", "full", CheckStatus(Severity.OK, "sensor", "nominal")),
        ]
        overall = CheckStatus(Severity.ERROR, "overall", "3 topic(s)")
        app = TestudoApp(StaticDataSource(reports, overall), ros_distro="jazzy", sim_time_active=False)
        async with app.run_test() as pilot:
            categories = app.screen.query_one(CategorySummaryTable)
            # "Other Topics" holds the ERROR-severity /battery -- worst
            # overall -- yet must still sort last, not first.
            names = [categories.get_row_at(i)[0] for i in range(categories.row_count)]
            assert names[-1] == "Other Topics"

            await pilot.press("s")
            names_after_sort_toggle = [categories.get_row_at(i)[0] for i in range(categories.row_count)]
            assert names_after_sort_toggle[-1] == "Other Topics"

    run(body())


def test_odometry_category_shows_a_plugin_defined_extra_column() -> None:
    async def body():
        app = _make_app()
        async with app.run_test():
            # Odometry (ERROR, worst) is selected by default -- see _make_app.
            topics = app.screen.query_one(TopicDetailTable)
            assert len(topics.ordered_columns) == 4
            assert str(topics.ordered_columns[-1].label) == "Cov"
            assert topics.get_row_at(0)[-1] == "[bold red]0.6[/bold red]"

    run(body())


def test_extra_column_disappears_for_a_category_without_one() -> None:
    async def body():
        app = _make_app()
        async with app.run_test() as pilot:
            await pilot.press("j")  # move off "Odometry" onto the next category
            topics = app.screen.query_one(TopicDetailTable)
            categories = app.screen.query_one(CategorySummaryTable)
            assert categories.selected_category != "Odometry"
            assert len(topics.ordered_columns) == 3

    run(body())


def test_detail_pane_shows_active_error_codes_not_the_free_text_message() -> None:
    async def body():
        reports = [
            TopicReport(
                "/odom",
                "nav_msgs/msg/Odometry",
                "full",
                CheckStatus(
                    Severity.ERROR,
                    "odometry",
                    "covariance too high",  # must NOT appear in the Detail Panel
                    codes={"ODOM-001": "[bold red]position covariance trace 0.6 out of bounds[/bold red]"},
                ),
            ),
        ]
        overall = CheckStatus(Severity.ERROR, "overall", "1 topic(s)")
        app = TestudoApp(StaticDataSource(reports, overall), ros_distro="jazzy", sim_time_active=False)
        async with app.run_test():
            detail = str(app.screen.query_one("#detail-pane").content)
            assert "ODOM-001" in detail
            assert "position covariance trace 0.6 out of bounds" in detail
            assert "covariance too high" not in detail

    run(body())
