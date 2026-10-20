"""The dashboard: categories, that category's topics, and the highlighted
topic's full detail, all visible and updating live at once -- moving the
cursor (not pressing Enter) is what drives the other panes, since the
point is monitoring several things at a glance rather than a list you
have to select into one entry at a time. Plus a help overlay.
"""
from __future__ import annotations

from collections import Counter

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import DataTable, Input, Static

from testudo.tui.categorize import category_for
from testudo.tui.data_source import WatchSnapshot
from testudo.tui.detail_render import render_detail
from testudo.tui.pane_split import DETAIL_CHROME_ROWS, detail_cap, detail_height, rendered_rows
from testudo.tui.keybinds import (
    COPY_BINDING,
    FILTER_BINDING,
    HELP_TEXT,
    PANE_LEFT_BINDING,
    PANE_RIGHT_BINDING,
    SORT_BINDING,
)
from testudo.tui.widgets.attention import attention_text
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
        COPY_BINDING,
        PANE_LEFT_BINDING,
        PANE_RIGHT_BINDING,
        ("escape", "handle_escape", "Categories"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._filtering = False
        self._filtered_table: DataTable | None = None
        # The Detail Panel's last (topic, height), so it doesn't shrink
        # while the same topic stays highlighted (`pane_split.detail_height`).
        self._detail_sizing: tuple[str, int] | None = None

    def compose(self) -> ComposeResult:
        yield TestudoHeader(self.app.ros_distro, self.app.ros_domain_id, self.app.dds_implementation)
        with Horizontal(id="panes"):
            with Vertical(id="left-pane"):
                yield CategorySummaryTable(id="category-table")
                yield Static("", id="attention")
            yield TopicDetailTable(id="topic-table")
        yield Static("", id="detail-pane")
        yield Input(placeholder="filter by name, enter/esc to apply", id="filter-input")
        yield Static(self._hint_text(), id="hint")

    def on_mount(self) -> None:
        self.query_one("#filter-input", Input).display = False
        self.sync_header()
        self.refresh_categories()
        self.query_one(CategorySummaryTable).focus()
        # Nothing has a size until the first layout: size the Detail Panel then.
        self.call_after_refresh(self.refresh_detail)

    def _hint_text(self) -> str:
        # One line even at 101 columns (the narrowest typical terminal):
        # `r` (reset) is left to the help overlay, the rest is what a
        # first-time user needs to find their way around.
        return "j/k move · tab/←→ pane · / filter · s sort · p pause · o options · ? help · q quit"

    def sync_header(self) -> None:
        header = self.query_one(TestudoHeader)
        header.sim_time_active = self.app.sim_time_active
        header.paused = self.app.paused
        snapshot = self.app.latest_snapshot
        # Same scope as `overall` itself: topics only, not node-profiling rows.
        counts = Counter(report.status.severity for report in snapshot.reports if report.tier != "node")
        header.verdict = (snapshot.overall, dict(counts))

    def on_snapshot(self, snapshot: WatchSnapshot) -> None:
        self.sync_header()
        self.refresh_categories()

    def refresh_categories(self) -> None:
        self.query_one(CategorySummaryTable).update_categories(self.app.latest_snapshot.reports)
        self.refresh_attention()
        self.refresh_topics()

    def refresh_attention(self) -> None:
        """Fill the Plugin Panel's free rows with the "Needs attention" list (hidden when there are none)."""
        attention = self.query_one("#attention", Static)
        text = attention_text(
            self.app.latest_snapshot.reports, attention.content_size.width, attention.content_size.height
        )
        attention.update(text or "")

    def refresh_topics(self) -> None:
        category = self.query_one(CategorySummaryTable).selected_category
        reports = [r for r in self.app.latest_snapshot.reports if category is not None and category_for(r) == category]
        self.query_one(TopicDetailTable).update_topics(reports)
        self.refresh_detail()

    def refresh_detail(self) -> None:
        """Size the Detail Panel to the highlighted topic's content, then render it at that size.

        Layout (`detail_render.render_detail`) depends on the pane's width
        and height -- how many columns the value grid flows into, and
        which of a plugin's wide/`_compact` value pairs fits -- so it is
        recomputed here on every refresh, terminal resizes included (see
        `on_resize` below), not fixed at the status's own construction.
        The height itself comes from `pane_split`: the rows the content
        needs, the panes above getting the rest.
        """
        detail = self.query_one("#detail-pane", Static)
        report = self.query_one(TopicDetailTable).selected_report
        if report is None:
            detail.update("[dim]no topic selected[/dim]")
            return
        width = detail.size.width
        available = self.query_one("#panes").outer_size.height + detail.outer_size.height
        if available <= 0 or width <= 0:
            # Not laid out yet (see on_mount): render for an unknown height.
            detail.update(render_detail(report, width, detail.size.height or None))
            return

        panes_wanted = 1 + max(
            self.query_one(CategorySummaryTable).row_count, self.query_one(TopicDetailTable).row_count
        )
        cap = detail_cap(available, panes_wanted)
        markup = render_detail(report, width, cap - DETAIL_CHROME_ROWS)
        previous = self._detail_sizing[1] if self._detail_sizing and self._detail_sizing[0] == report.topic else None
        height = detail_height(cap, rendered_rows(markup, width), previous)
        self._detail_sizing = (report.topic, height)
        if height != detail.outer_size.height:
            detail.styles.height = height
            # The Plugin Panel's free rows (the "Needs attention" list) change with it.
            self.call_after_refresh(self.refresh_attention)
        detail.update(render_detail(report, width, height - DETAIL_CHROME_ROWS))

    def on_resize(self, event) -> None:
        # The detail pane's size (which render_detail lays out for) just
        # changed -- redraw so the value grid and a wide<->narrow meter
        # swap aren't stuck at whatever fit before the resize. Same for the
        # Plugin Panel's free rows under its categories.
        self.refresh_attention()
        self.refresh_detail()

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

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Decline the priority Left/Right pane-switch while the filter input is focused.

        `PANE_LEFT_BINDING`/`PANE_RIGHT_BINDING` are priority bindings --
        the only way to preempt DataTable's own Left/Right (a harmless
        scroll no-op in row-cursor mode, but still first in line for the
        key otherwise). Declining here (returning False) makes Textual
        fall through to normal, focus-based dispatch instead of treating
        the key as handled, so the filter `Input` still gets it -- moving
        its text cursor left/right while typing a filter query, rather
        than the keystroke silently switching panes out from under it.
        """
        if action in ("focus_previous_pane", "focus_next_pane") and self._filtering:
            return False
        return True

    def action_focus_previous_pane(self) -> None:
        self.query_one(CategorySummaryTable).focus()

    def action_focus_next_pane(self) -> None:
        self.query_one(TopicDetailTable).focus()

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

    def action_toggle_sort(self) -> None:
        table = self._focused_table()
        if table is None:
            return
        table.set_sort_by_severity(not table.sort_by_severity)
        self._refresh_from(table)

    def action_copy_topic(self) -> None:
        report = self.query_one(TopicDetailTable).selected_report
        if report is None:
            return
        self.app.copy_to_clipboard(report.topic)
        self.notify(f"copied {report.topic}", timeout=2)

    def _refresh_from(self, table: DataTable) -> None:
        if isinstance(table, CategorySummaryTable):
            self.refresh_categories()
        else:
            self.refresh_topics()
