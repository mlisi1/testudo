"""The dashboard: categories, that category's topics, and the highlighted
topic's full detail, all visible and updating live at once -- moving the
cursor (not pressing Enter) is what drives the other panes, since the
point is monitoring several things at a glance rather than a list you
have to select into one entry at a time. Plus a help overlay.
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import ModalScreen, Screen
from textual.widgets import DataTable, Input, Static

from testudo.tui.categorize import category_for
from testudo.tui.data_source import WatchSnapshot
from testudo.tui.keybinds import FILTER_BINDING, HELP_TEXT, SORT_BINDING
from testudo.tui.widgets.category_summary import CategorySummaryTable
from testudo.tui.widgets.header import TestudoHeader
from testudo.tui.widgets.topic_panel import TopicDetailTable


class HelpScreen(ModalScreen):
    """`?`: a full-screen overlay listing every keybind. Any key closes it."""

    def compose(self) -> ComposeResult:
        yield Static(HELP_TEXT, id="help-text")

    def on_key(self, event) -> None:
        self.dismiss()


class DashboardScreen(Screen):
    """Categories | topics side by side, with a detail pane below for
    whichever topic is currently highlighted -- three views of the data
    on screen together, none of them requiring a keypress to populate.
    """

    BINDINGS = [
        FILTER_BINDING,
        SORT_BINDING,
        ("escape", "handle_escape", "Categories"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._filtering = False
        self._filtered_table: DataTable | None = None

    def compose(self) -> ComposeResult:
        yield TestudoHeader(self.app.ros_distro)
        with Horizontal(id="panes"):
            yield CategorySummaryTable(id="category-table")
            yield TopicDetailTable(id="topic-table")
        yield Static("", id="detail-pane")
        yield Input(placeholder="filter by name, enter/esc to apply", id="filter-input")
        yield Static(self._hint_text(), id="hint")

    def on_mount(self) -> None:
        self.query_one("#filter-input", Input).display = False
        self.sync_header()
        self.refresh_categories()
        self.query_one(CategorySummaryTable).focus()

    def _hint_text(self) -> str:
        return (
            "arrows/j/k: move (updates the panes live)   tab: switch pane   "
            "/: filter   s: sort   p: pause   r: reset   ?: help   q: quit"
        )

    def sync_header(self) -> None:
        header = self.query_one(TestudoHeader)
        header.sim_time_active = self.app.sim_time_active
        header.paused = self.app.paused

    def on_snapshot(self, snapshot: WatchSnapshot) -> None:
        self.sync_header()
        self.refresh_categories()

    def refresh_categories(self) -> None:
        self.query_one(CategorySummaryTable).update_categories(self.app.latest_snapshot.reports)
        self.refresh_topics()

    def refresh_topics(self) -> None:
        category = self.query_one(CategorySummaryTable).selected_category
        reports = [r for r in self.app.latest_snapshot.reports if category is not None and category_for(r) == category]
        self.query_one(TopicDetailTable).update_topics(reports)
        self.refresh_detail()

    def refresh_detail(self) -> None:
        detail = self.query_one("#detail-pane", Static)
        report = self.query_one(TopicDetailTable).selected_report
        if report is None:
            detail.update("[dim]no topic selected[/dim]")
            return
        lines = [f"[b]{report.topic}[/b]  ({report.msg_type}, {report.tier} tier)", "", report.status.message]
        if report.status.values:
            lines.append("")
            lines.extend(f"  {key}: {value}" for key, value in report.status.values.items())
        detail.update("\n".join(lines))

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if isinstance(event.data_table, CategorySummaryTable):
            self.refresh_topics()
        elif isinstance(event.data_table, TopicDetailTable):
            self.refresh_detail()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        # Enter jumps focus to "the next pane in" -- categories -> topics ->
        # back to categories -- since both are already visible and live, there's
        # no separate screen left to "drill into".
        if isinstance(event.data_table, CategorySummaryTable):
            self.query_one(TopicDetailTable).focus()
        elif isinstance(event.data_table, TopicDetailTable):
            self.query_one(CategorySummaryTable).focus()

    def _focused_table(self) -> DataTable | None:
        focused = self.focused
        return focused if isinstance(focused, (CategorySummaryTable, TopicDetailTable)) else None

    def action_start_filter(self) -> None:
        table = self._focused_table()
        if table is None:
            return
        self._filtered_table = table
        filter_input = self.query_one("#filter-input", Input)
        filter_input.display = True
        self._filtering = True
        filter_input.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._stop_filtering()

    def on_input_changed(self, event: Input.Changed) -> None:
        if self._filtered_table is not None:
            self._filtered_table.set_filter(event.value)
            self._refresh_from(self._filtered_table)

    def _stop_filtering(self) -> None:
        filter_input = self.query_one("#filter-input", Input)
        filter_input.display = False
        self._filtering = False
        self.set_focus(self._filtered_table or self.query_one(CategorySummaryTable))

    def action_handle_escape(self) -> None:
        if self._filtering:
            filter_input = self.query_one("#filter-input", Input)
            filter_input.value = ""
            if self._filtered_table is not None:
                self._filtered_table.set_filter("")
                self._refresh_from(self._filtered_table)
            self._stop_filtering()
            return
        self.query_one(CategorySummaryTable).focus()

    def action_cycle_sort(self) -> None:
        table = self._focused_table()
        if table is None:
            return
        table.set_sort_by_severity(not table.sort_by_severity)
        self._refresh_from(table)

    def _refresh_from(self, table: DataTable) -> None:
        if isinstance(table, CategorySummaryTable):
            self.refresh_categories()
        else:
            self.refresh_topics()
