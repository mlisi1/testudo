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
from testudo.tui.keybinds import FILTER_BINDING, HELP_TEXT, PANE_LEFT_BINDING, PANE_RIGHT_BINDING, SORT_BINDING
from testudo.tui.widgets.category_summary import CategorySummaryTable
from testudo.tui.widgets.header import TestudoHeader
from testudo.tui.widgets.topic_panel import TopicDetailTable

#: `CheckStatus.values` key suffix marking a value's narrow-terminal
#: alternative -- e.g. a plugin emits both "gauge" (a wide multi-segment
#: meter) and "gauge_compact" (a single colored square) for the same
#: metric, and `_resolve_values` below picks whichever fits the Detail
#: Panel's current width. A plain naming convention, not a CheckStatus
#: schema change (TUI_DATA_DESIGN.md's Track A) -- any plugin can opt in.
COMPACT_VALUE_SUFFIX = "_compact"

#: Detail Panel width (columns) below which `_resolve_values` prefers a
#: value's `_compact` variant over its full one, where both exist.
NARROW_DETAIL_WIDTH = 60


def _resolve_values(values: dict[str, str], is_narrow: bool) -> list[tuple[str, str]]:
    """Collapse `<key>`/`<key>_compact` pairs in `values` down to one entry each.

    Preserves `values`' insertion order. A key with no compact counterpart
    (most of them) passes through unchanged, from either side of the pair.
    """
    resolved: list[tuple[str, str]] = []
    skip: set[str] = set()
    for key, value in values.items():
        if key in skip:
            continue
        if key.endswith(COMPACT_VALUE_SUFFIX):
            base = key[: -len(COMPACT_VALUE_SUFFIX)]
            skip.add(base)
            if is_narrow:
                resolved.append((base, value))
            continue
        compact_key = f"{key}{COMPACT_VALUE_SUFFIX}"
        if compact_key in values:
            skip.add(compact_key)
            resolved.append((key, values[compact_key] if is_narrow else value))
            continue
        resolved.append((key, value))
    return resolved


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
        PANE_LEFT_BINDING,
        PANE_RIGHT_BINDING,
        ("escape", "handle_escape", "Categories"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._filtering = False
        self._filtered_table: DataTable | None = None

    def compose(self) -> ComposeResult:
        yield TestudoHeader(self.app.ros_distro, self.app.ros_domain_id, self.app.dds_implementation)
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
            "arrows/j/k: move   tab/←→: switch pane   /: filter   s: sort   "
            "p: pause   r: reset   ?: help   q: quit"
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
        """Header, then structured data, then the active-error-code list last.

        `status.message` (the single rolled-up free-text summary) is
        deliberately not shown here -- `status.codes` (TUI_DATA_DESIGN.md's
        error-code proposal) supersedes it: every currently active problem
        gets its own `[CODE] message` line, independently of the others,
        rather than one string with them all run together. `values` is
        rendered at `#detail-pane`'s *current* width via `_resolve_values`,
        so a plugin's wide/`_compact` value pairs adapt live to terminal
        resizes (see `on_resize` below), not just at the moment of the
        status's own construction.
        """
        detail = self.query_one("#detail-pane", Static)
        report = self.query_one(TopicDetailTable).selected_report
        if report is None:
            detail.update("[dim]no topic selected[/dim]")
            return
        lines = [f"[b]{report.topic}[/b]  ({report.msg_type}, {report.tier} tier)"]
        if report.status.values:
            is_narrow = detail.size.width < NARROW_DETAIL_WIDTH
            for key, value in _resolve_values(report.status.values, is_narrow):
                # A value containing its own newlines is a pre-formatted,
                # self-labeled multi-line block (Track A) -- shown as-is,
                # without a redundant "key: " prefix on its first line.
                lines.append(value if "\n" in value else f"{key}: {value}")
        if report.status.codes:
            lines.append("")
            lines.extend(f"[b]\\[{code}][/b] {message}" for code, message in report.status.codes.items())
        detail.update("\n".join(lines))

    def on_resize(self, event) -> None:
        # The detail pane's width (used by _resolve_values above) just
        # changed -- redraw so a wide<->narrow meter swap isn't stuck
        # showing whichever variant happened to be picked before the resize.
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
