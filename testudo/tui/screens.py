"""The three screens the app navigates between: category summary (the entry
point), a category's topic list (drill-down), and one topic's full detail
(a modal), plus a help overlay.
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import ModalScreen, Screen
from textual.widgets import DataTable, Input, Static

from testudo.core.topic_report import TopicReport
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


class TopicDetailScreen(ModalScreen):
    """Enter on a topic row: its full status message + every value. Esc closes it."""

    BINDINGS = [("escape", "dismiss", "Back")]

    def __init__(self, report: TopicReport) -> None:
        super().__init__()
        self._report = report

    def compose(self) -> ComposeResult:
        lines = [
            f"[b]{self._report.topic}[/b]  ({self._report.msg_type}, {self._report.tier} tier)",
            "",
            self._report.status.message,
        ]
        if self._report.status.values:
            lines.append("")
            lines.extend(f"  {key}: {value}" for key, value in self._report.status.values.items())
        yield Static("\n".join(lines), id="topic-detail")


class TableScreen(Screen):
    """Shared filter/sort/header-sync behavior for the summary and category screens."""

    BINDINGS = [FILTER_BINDING, SORT_BINDING, ("escape", "go_back", "Back")]

    def __init__(self) -> None:
        super().__init__()
        self._sort_by_severity = True
        self._filtering = False

    def compose(self) -> ComposeResult:
        yield TestudoHeader(self.app.ros_distro)
        yield self._build_table()
        yield Input(placeholder="filter by name, enter/esc to apply", id="filter-input")
        yield Static(self._hint_text(), id="hint")

    def on_mount(self) -> None:
        self.query_one("#filter-input", Input).display = False
        self.sync_header()
        self.refresh_table()
        self.set_focus(self._table())

    def sync_header(self) -> None:
        header = self.query_one(TestudoHeader)
        header.sim_time_active = self.app.sim_time_active
        header.paused = self.app.paused
        header.live = self.app.data_source.is_live()

    def on_snapshot(self, snapshot: WatchSnapshot) -> None:
        self.sync_header()
        self.refresh_table()

    def action_start_filter(self) -> None:
        filter_input = self.query_one("#filter-input", Input)
        filter_input.display = True
        self._filtering = True
        filter_input.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._stop_filtering()

    def on_input_changed(self, event: Input.Changed) -> None:
        self._table().set_filter(event.value)
        self.refresh_table()

    def _stop_filtering(self) -> None:
        filter_input = self.query_one("#filter-input", Input)
        filter_input.display = False
        self._filtering = False
        self.set_focus(self._table())

    def action_go_back(self) -> None:
        if self._filtering:
            filter_input = self.query_one("#filter-input", Input)
            filter_input.value = ""
            self._table().set_filter("")
            self.refresh_table()
            self._stop_filtering()
            return
        self.go_back()

    def action_cycle_sort(self) -> None:
        self._sort_by_severity = not self._sort_by_severity
        self._table().set_sort_by_severity(self._sort_by_severity)
        self.refresh_table()

    # -- overridden by subclasses --

    def _build_table(self) -> DataTable:
        raise NotImplementedError

    def _table(self) -> DataTable:
        raise NotImplementedError

    def refresh_table(self) -> None:
        raise NotImplementedError

    def _hint_text(self) -> str:
        return "enter: drill down   /: filter   s: sort   ?: help   q: quit"

    def go_back(self) -> None:
        """Default: nothing to go back to. Overridden by CategoryScreen."""


class SummaryScreen(TableScreen):
    """The top-level view: one row per category."""

    def _build_table(self) -> DataTable:
        return CategorySummaryTable(id="category-table")

    def _table(self) -> CategorySummaryTable:
        return self.query_one(CategorySummaryTable)

    def refresh_table(self) -> None:
        self._table().update_categories(self.app.latest_snapshot.reports)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        category = self._table().selected_category
        if category is not None:
            self.app.push_screen(CategoryScreen(category))


class CategoryScreen(TableScreen):
    """Drill-down: every topic in one category."""

    def __init__(self, category: str) -> None:
        super().__init__()
        self._category = category

    def _build_table(self) -> DataTable:
        return TopicDetailTable(id="topic-table")

    def _table(self) -> TopicDetailTable:
        return self.query_one(TopicDetailTable)

    def refresh_table(self) -> None:
        reports = [r for r in self.app.latest_snapshot.reports if category_for(r) == self._category]
        self._table().update_topics(reports)

    def _hint_text(self) -> str:
        return f"{self._category}   |   enter: details   esc: back   /: filter   s: sort   ?: help   q: quit"

    def go_back(self) -> None:
        self.app.pop_screen()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        report = self._table().selected_report
        if report is not None:
            self.app.push_screen(TopicDetailScreen(report))
