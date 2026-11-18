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

from testudo.core.topic_report import TopicReport
from testudo.tui.data_source import WatchSnapshot
from testudo.tui.display_prefs import WORST_FIRST, shows_topic, visible_reports
from testudo.tui.detail_render import render_detail
from testudo.tui.pane_split import DETAIL_CHROME_ROWS, detail_cap, detail_height, rendered_rows
from testudo.tui.keybinds import (
    COPY_BINDING,
    FILTER_BINDING,
    HELP_TEXT,
    NEXT_PROBLEM_BINDING,
    PANE_LEFT_BINDING,
    PANE_RIGHT_BINDING,
    PREVIOUS_PROBLEM_BINDING,
    QUICK_ACTIONS_BINDING,
    SORT_BINDING,
)
from testudo.tui.categorize import category_for
from testudo.tui.quick_actions import QuickActionBar, can_act_on
from testudo.tui.widgets.attention import attention_text, problem_topics
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
        NEXT_PROBLEM_BINDING,
        PREVIOUS_PROBLEM_BINDING,
        QUICK_ACTIONS_BINDING,
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
        yield QuickActionBar(id="quick-actions")
        yield Static(self._hint_text(), id="hint")

    def on_mount(self) -> None:
        self.query_one("#filter-input", Input).display = False
        self.sync_header()
        self.apply_display_prefs()
        self.query_one(CategorySummaryTable).focus()
        # Nothing has a size until the first layout: size the Detail Panel then.
        self.call_after_refresh(self.refresh_detail)

    def _hint_text(self) -> str:
        # One line even at 101 columns (the narrowest typical terminal):
        # `r` (reset) is left to the help overlay, the rest is what a
        # first-time user needs to find their way around.
        if self.app.timeline is not None:
            # A replay's options are read-only: its slot goes to scrubbing.
            return "j/k move · tab/←→ pane · / search · n problem · s sort · p play · [ ] scrub · ? help · q quit"
        return "j/k move · tab/←→ pane · / search · n problem · a actions · p pause · o options · ? help · q quit"

    def apply_display_prefs(self) -> None:
        """Show the data the way Options > Display asks: default sort, hidden rows."""
        worst_first = self.app.display_prefs.default_sort == WORST_FIRST
        self.query_one(CategorySummaryTable).set_sort_by_severity(worst_first)
        self.query_one(TopicDetailTable).set_sort_by_severity(worst_first)
        self.refresh_categories()

    def _visible_reports(self) -> list[TopicReport]:
        return visible_reports(self.app.latest_snapshot.reports, self.app.display_prefs)

    def sync_header(self) -> None:
        header = self.query_one(TestudoHeader)
        header.sim_time_active = self.app.sim_time_active
        header.paused = self.app.paused
        timeline = self.app.timeline
        if timeline is not None:
            header.replay = (timeline.position_s, timeline.duration_s, timeline.playing)
        snapshot = self.app.latest_snapshot
        # Same scope as `overall` itself: topics only, not node-profiling rows.
        counts = Counter(report.status.severity for report in snapshot.reports if report.tier != "node")
        header.verdict = (snapshot.overall, dict(counts))

    def on_snapshot(self, snapshot: WatchSnapshot) -> None:
        self.sync_header()
        self.refresh_categories()

    def refresh_categories(self) -> None:
        self.query_one(CategorySummaryTable).update_categories(self._visible_reports())
        self.refresh_attention()
        self.refresh_topics()

    def refresh_attention(self) -> None:
        """Fill the Plugin Panel's free rows with the "Needs attention" list (hidden when there are none)."""
        attention = self.query_one("#attention", Static)
        snapshot = self.app.latest_snapshot
        reports = self._visible_reports()
        hidden = {report.topic for report in snapshot.reports} - {report.topic for report in reports}
        text = attention_text(
            reports,
            attention.content_size.width,
            attention.content_size.height,
            tuple(fault for fault in snapshot.cleared_faults if fault.topic not in hidden),
            snapshot.time_s,
        )
        attention.update(text or "")

    def refresh_topics(self) -> None:
        categories = self.query_one(CategorySummaryTable)
        reports = categories.reports_in(categories.selected_category, self._visible_reports())
        prefs = self.app.display_prefs
        self.query_one(TopicDetailTable).update_topics([report for report in reports if shows_topic(report, prefs)])
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
            hiding = self.app.display_prefs.hide_ok_topics
            detail.update("[dim]every topic here is OK (hidden: Options > Display)[/dim]" if hiding else "[dim]no topic selected[/dim]")
            return
        width = detail.size.width
        available = self.query_one("#panes").outer_size.height + detail.outer_size.height
        snapshot = self.app.latest_snapshot
        cleared = [fault for fault in snapshot.cleared_faults if fault.topic == report.topic]

        def render(height: int | None) -> str:
            return render_detail(report, width, height, cleared, snapshot.time_s)

        if available <= 0 or width <= 0:
            # Not laid out yet (see on_mount): render for an unknown height.
            detail.update(render(detail.size.height or None))
            return

        panes_wanted = 1 + max(
            self.query_one(CategorySummaryTable).row_count, self.query_one(TopicDetailTable).row_count
        )
        cap = detail_cap(available, panes_wanted)
        markup = render(cap - DETAIL_CHROME_ROWS)
        previous = self._detail_sizing[1] if self._detail_sizing and self._detail_sizing[0] == report.topic else None
        height = detail_height(cap, rendered_rows(markup, width), previous)
        self._detail_sizing = (report.topic, height)
        if height != detail.outer_size.height:
            detail.styles.height = height
            # The Plugin Panel's free rows (the "Needs attention" list) change with it.
            self.call_after_refresh(self.refresh_attention)
        detail.update(render(height - DETAIL_CHROME_ROWS))

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
        filter_input.placeholder = (
            "search topics and categories, enter/esc to apply"
            if isinstance(table, CategorySummaryTable)
            else "filter this category's topics, enter/esc to apply"
        )
        filter_input.display = True
        self._filtering = True
        filter_input.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._stop_filtering()

    def on_input_changed(self, event: Input.Changed) -> None:
        if self._filtered_table is not None:
            self._filtered_table.set_filter(event.value)
            self._refresh_from(self._filtered_table)
            if isinstance(self._filtered_table, CategorySummaryTable):
                # Land on the first match, so the Topic Panel shows it straight away.
                self._filtered_table.move_cursor(row=0)

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

    def action_quick_actions(self) -> None:
        """`a`: the highlighted topic's actions, in place of the hint bar until one is chosen."""
        report = self.query_one(TopicDetailTable).selected_report
        if self._filtering or report is None:
            return
        if not self.app.data_source.is_live():
            self.notify("a replay can't change the config", timeout=3)
        elif not can_act_on(report):
            self.notify("nothing to change on this row", timeout=3)
        else:
            self.query_one(QuickActionBar).open(report, self.app.data_source, self.focused)

    def action_jump_problem(self, step: int) -> None:
        """`n`/`N`: highlight the next/previous topic with an active code, whatever its category.

        Walks `problem_topics` (the "Needs attention" order), wrapping
        around, starting from the highlighted topic -- or from the first
        (last, for `N`) when the highlighted topic isn't one of them. A
        filter hiding the target is cleared rather than skipping it.
        """
        if self._filtering:
            return
        reports = self._visible_reports()
        problems = problem_topics(reports)
        if not problems:
            self.notify("nothing needs attention", timeout=2)
            return
        categories = self.query_one(CategorySummaryTable)
        topics = self.query_one(TopicDetailTable)
        current = topics.selected_topic
        if current in problems:
            target = problems[(problems.index(current) + step) % len(problems)]
        else:
            target = problems[0] if step > 0 else problems[-1]
        category = next(category_for(report) for report in reports if report.topic == target)

        if not categories.select_category(category) or target not in [
            report.topic for report in categories.reports_in(category, reports)
        ]:
            self._clear_filters()
            categories.select_category(category)
        # The category cursor's RowHighlighted only arrives later: refresh
        # the Topic Panel for it now, so the target row exists to move onto.
        self.refresh_topics()
        if not topics.select_topic(target):
            self._clear_filters()
            self.refresh_topics()
            topics.select_topic(target)
        topics.focus()
        self.refresh_detail()

    def _clear_filters(self) -> None:
        # Detached first: the Input.Changed this value change posts arrives
        # after the jump, and would otherwise put the category cursor back
        # on row 0 (`on_input_changed`).
        self._filtered_table = None
        self.query_one("#filter-input", Input).value = ""
        for table in (self.query_one(CategorySummaryTable), self.query_one(TopicDetailTable)):
            table.set_filter("")
        self.refresh_categories()

    def _refresh_from(self, table: DataTable) -> None:
        if isinstance(table, CategorySummaryTable):
            self.refresh_categories()
        else:
            self.refresh_topics()
