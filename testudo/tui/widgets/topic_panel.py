"""Topic Panel: every topic within one selected category."""
from __future__ import annotations

from testudo.core.topic_report import TopicReport
from testudo.tui.categorize import OTHER_CATEGORY, category_for
from testudo.tui.keybinds import NavDataTable
from testudo.tui.widgets.topic_cells import (
    common_topic_prefix,
    display_name,
    marquee_window,
    next_marquee_offset,
    rate_cell,
    rate_column_header,
    status_cell,
    tail_first,
    topic_header,
    type_cell,
)

#: Smallest text capacity of the "Topic" column. The column takes whatever
#: width the pane has left after the other columns (see
#: `TopicDetailTable._fit_topic_column`), never less than this. Names are
#: shown with the category's shared namespace stripped (it's in the column
#: header instead), and a name still too long keeps its *tail* (see
#: `tail_first`); only one whose last segment alone doesn't fit scrolls
#: back and forth (see `_advance_marquee`). Sizing the column from the
#: pane, not from its content, is what leaves room for Status/Hz without
#: a horizontal scrollbar.
TOPIC_COLUMN_WIDTH = 16

STATUS_COLUMN_WIDTH = 9

RATE_COLUMN_WIDTH = 6

#: Columns reserved for the vertical scrollbar whether or not it's showing,
#: so the Topic column doesn't change width as rows come and go.
_SCROLLBAR_WIDTH = 2

#: Width of the optional plugin-defined extra column (`CheckStatus.
#: topic_panel_column`/`topic_panel_value`) -- short by design, same budget
#: class as Status/Hz, never a sentence.
EXTRA_COLUMN_WIDTH = 10

#: Other Topics has no plugin to define an extra column, so it shows each
#: topic's message type there instead: the one thing a vitals-tier row
#: knows besides its rate. Wider than a plugin's column (a type name is
#: rarely under 20 characters), narrowed on a small pane so the Topic
#: column keeps at least `_TOPIC_WIDTH_BEFORE_NARROW_TYPE` (see `type_cell`
#: for how a type that still doesn't fit is shortened).
TYPE_COLUMN_HEADER = "Type"
TYPE_COLUMN_WIDTH = 24
NARROW_TYPE_COLUMN_WIDTH = 16
_TOPIC_WIDTH_BEFORE_NARROW_TYPE = 24

#: The extra column's fixed DataTable key. Its *label* changes per category
#: (whichever plugin currently owns the visible topics), so it's removed and
#: re-added with a new label rather than renamed in place.
EXTRA_COLUMN_KEY = "extra"

#: How often the marquee shifts by one character.
MARQUEE_INTERVAL_SECONDS = 0.4


class TopicDetailTable(NavDataTable):
    """One row per topic: name, status, publish rate, plus an optional
    plugin-defined extra column.

    No "tier" column here -- it's shown in the detail pane's header line
    instead (`/topic  (msg_type, tier tier)`), freeing that width for
    Topic/Status/Hz, the fields that matter for scanning a list. The full
    status message is only shown in the detail pane below, not here.

    Status/Hz (and the extra column) are fixed-width; Topic takes all the
    width the pane has left over, re-fitted on resize, so a wide terminal
    shows full topic names instead of a 16-character window beside a
    mostly empty pane.

    Hz comes straight from `TopicVitals.rate_hz()`, which is already
    updated on every message arrival for every subscribed topic regardless
    of tier or content-check decimation -- displaying it here is free, not
    an extra measurement. Two categories swap it for something that means
    more there (see `rate_column_header`): Nodes shows memory, Nav2
    Actions the server's current state. Nav2 Actions rows are also named
    for the action, not its `/_action/status` topic (`display_name`).

    A 4th column appears when the visible category's reports carry a
    `CheckStatus.topic_panel_column` (e.g. odometry's "Cov"), and disappears
    again for a category that doesn't. "Other Topics" uses it for each
    topic's message type instead (`type_cell`) -- see
    `_extra_column_header_for`/`_fit_topic_column`. A topic currently
    liveness-overridden (stale/no-messages) has no `topic_panel_column` of
    its own even within such a category; its cell just reads "-".

    See `CategorySummaryTable`'s docstring for why routine updates avoid
    `clear()` (it resets scroll position and cursor, reading as flicker on
    a scrolled, actively-updating table): a full rebuild only happens when
    the visible topic *set* changes, the extra column's presence/label
    changes, or sort order was just toggled.
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.cursor_type = "row"
        self._topic_order: list[str] = []
        self._reports_by_topic: dict[str, TopicReport] = {}
        self._filter_text = ""
        self._sort_by_severity = True
        self._needs_rebuild = True
        self._marquee_offset: dict[str, int] = {}
        self._marquee_direction: dict[str, int] = {}
        self._extra_column_header: str | None = None
        # "Hz" for most categories; see `rate_column_header`.
        self._rate_column_header = "Hz"
        self._topic_width = TOPIC_COLUMN_WIDTH
        self._extra_width = EXTRA_COLUMN_WIDTH
        # Namespace shared by every visible topic, stripped from each row
        # and shown once in the column header instead (see `common_topic_prefix`).
        self._topic_prefix = ""

    def on_mount(self) -> None:
        self._add_columns()
        self.set_interval(MARQUEE_INTERVAL_SECONDS, self._advance_marquee)

    def _add_columns(self) -> None:
        # Fixed widths, not auto (content-fit): an auto "Topic" column grows
        # to fit the single longest topic name it's ever shown, which was
        # free to dominate the row and push the other columns off-screen
        # (needing a horizontal scroll to see them at all). The full,
        # untruncated message is always available in the detail pane below.
        self.add_column(topic_header(self._topic_prefix, self._topic_width), key="topic", width=self._topic_width)
        self.add_column("Status", key="status", width=STATUS_COLUMN_WIDTH)
        self.add_column(self._rate_column_header, key="rate", width=RATE_COLUMN_WIDTH)
        if self._extra_column_header is not None:
            self.add_column(self._extra_column_header, key=EXTRA_COLUMN_KEY, width=self._extra_width)

    def _extra_width_for(self, pane_width: int) -> int:
        """The extra column's width: a plugin's fixed budget, or the Type column's (narrowed on a small pane)."""
        if self._extra_column_header != TYPE_COLUMN_HEADER:
            return EXTRA_COLUMN_WIDTH
        if self._topic_width_for(pane_width, TYPE_COLUMN_WIDTH) >= _TOPIC_WIDTH_BEFORE_NARROW_TYPE:
            return TYPE_COLUMN_WIDTH
        return NARROW_TYPE_COLUMN_WIDTH

    def _topic_width_for(self, pane_width: int, extra_width: int | None = None) -> int:
        """The Topic column's text capacity in a `pane_width`-wide table: all the width the others don't use."""
        other_widths = [STATUS_COLUMN_WIDTH, RATE_COLUMN_WIDTH]
        if self._extra_column_header is not None:
            other_widths.append(extra_width if extra_width is not None else self._extra_width_for(pane_width))
        padding = 2 * self.cell_padding * (len(other_widths) + 1)
        return max(TOPIC_COLUMN_WIDTH, pane_width - sum(other_widths) - padding - _SCROLLBAR_WIDTH)

    def _fit_topic_column(self, *, columns_changed: bool = False) -> None:
        """Re-create the columns if the Topic column's width no longer matches the pane.

        A column's width is fixed when it's added, so a different width
        means clearing and re-adding every column -- only done when the
        pane was resized or (`columns_changed`) the extra column came,
        went, or changed label, all rare, and `_rebuild` restores the
        selection afterward.
        """
        topic_width = self._topic_width_for(self.size.width)
        extra_width = self._extra_width_for(self.size.width)
        if (topic_width, extra_width) == (self._topic_width, self._extra_width) and not columns_changed:
            return
        self._topic_width = topic_width
        self._extra_width = extra_width
        self._marquee_offset.clear()
        self._marquee_direction.clear()
        previous_selection = self.selected_topic
        self.clear(columns=True)
        self._add_columns()
        self._rebuild(self._reports_by_topic, previous_selection)

    def on_resize(self, event) -> None:
        self._fit_topic_column()

    @property
    def sort_by_severity(self) -> bool:
        return self._sort_by_severity

    def set_filter(self, text: str) -> None:
        self._filter_text = text.strip().lower()

    def set_sort_by_severity(self, sort_by_severity: bool) -> None:
        if sort_by_severity != self._sort_by_severity:
            self._sort_by_severity = sort_by_severity
            self._needs_rebuild = True

    def update_topics(self, reports: list[TopicReport]) -> None:
        """Refresh from `reports` (already filtered to one category), preserving scroll/selection."""
        visible = {
            report.topic: report
            for report in reports
            if not self._filter_text or self._filter_text in report.topic.lower()
        }

        extra_header = self._extra_column_header_for(visible.values())
        rate_header = next((rate_column_header(report) for report in visible.values()), "Hz")
        prefix = common_topic_prefix(sorted(visible))
        if (extra_header, rate_header, prefix) != (self._extra_column_header, self._rate_column_header, self._topic_prefix):
            self._extra_column_header = extra_header
            self._rate_column_header = rate_header
            self._topic_prefix = prefix
            self._reports_by_topic = visible
            self._fit_topic_column(columns_changed=True)
            return

        if self._needs_rebuild or set(self._topic_order) != set(visible):
            self._rebuild(visible)
            return

        self._reports_by_topic = visible
        for topic in self._topic_order:
            self._write_row(visible[topic])

    @staticmethod
    def _extra_column_header_for(reports) -> str | None:
        """The plugin-defined extra column header shared by `reports`, or None if none is set.

        A category is one plugin's topics, so every report's
        `topic_panel_column` agrees when set at all -- except a topic
        currently liveness-overridden (stale/no-messages), which reports ""
        instead of its plugin's header. The first non-empty header found
        wins, rather than requiring unanimous agreement, so the column
        doesn't flicker away just because one topic in the category is
        currently stale.
        """
        reports = list(reports)
        for report in reports:
            if report.status.topic_panel_column:
                return report.status.topic_panel_column
        if reports and all(category_for(report) == OTHER_CATEGORY for report in reports):
            return TYPE_COLUMN_HEADER
        return None

    def _rebuild(self, visible: dict[str, TopicReport], previous_selection: str | None = None) -> None:
        previous_selection = previous_selection or self.selected_topic
        if self._sort_by_severity:
            self._topic_order = sorted(visible, key=lambda t: (-visible[t].status.severity, t))
        else:
            self._topic_order = sorted(visible)
        self._reports_by_topic = visible

        # Drop marquee state for topics no longer shown, so it doesn't grow
        # unbounded over a long watch session with a changing topic set.
        self._marquee_offset = {t: v for t, v in self._marquee_offset.items() if t in visible}
        self._marquee_direction = {t: v for t, v in self._marquee_direction.items() if t in visible}

        self.clear()
        blank_row = ("", "", "", "") if self._extra_column_header is not None else ("", "", "")
        for topic in self._topic_order:
            self.add_row(*blank_row, key=topic)
            self._write_row(visible[topic])

        if previous_selection in self._topic_order:
            self.move_cursor(row=self._topic_order.index(previous_selection))
        self._needs_rebuild = False

    def _display_name(self, topic: str) -> str:
        return display_name(topic, self._topic_prefix)

    def _topic_cell(self, topic: str) -> str:
        """The Topic cell: the name (prefix stripped), tail-first if too long, scrolling only if even that fails."""
        name = self._display_name(topic)
        fitted = tail_first(name, self._topic_width)
        if fitted is not None:
            return fitted
        return marquee_window(name, self._topic_width, self._marquee_offset.get(topic, 0))

    def _write_row(self, report: TopicReport) -> None:
        self.update_cell(report.topic, "topic", self._topic_cell(report.topic))
        if report.tier == "excluded":
            # Excluded Topics shows only the name -- there's no status or
            # rate behind it (no subscription of any kind, by design), so
            # leaving these blank is more honest than an "OK"/"-" that
            # implies something was actually checked.
            self.update_cell(report.topic, "status", "")
            self.update_cell(report.topic, "rate", "")
            if self._extra_column_header is not None:
                self.update_cell(report.topic, EXTRA_COLUMN_KEY, "")
            return
        self.update_cell(report.topic, "status", status_cell(report))
        self.update_cell(report.topic, "rate", rate_cell(report, self._rate_column_header))
        if self._extra_column_header == TYPE_COLUMN_HEADER:
            self.update_cell(report.topic, EXTRA_COLUMN_KEY, type_cell(report.msg_type, self._extra_width))
        elif self._extra_column_header is not None:
            self.update_cell(report.topic, EXTRA_COLUMN_KEY, report.status.topic_panel_value or "-")

    def _advance_marquee(self) -> None:
        for topic in self._topic_order:
            name = self._display_name(topic)
            if tail_first(name, self._topic_width) is not None:
                continue
            offset, direction = next_marquee_offset(
                len(name),
                self._topic_width,
                self._marquee_offset.get(topic, 0),
                self._marquee_direction.get(topic, 1),
            )
            self._marquee_offset[topic] = offset
            self._marquee_direction[topic] = direction
            self.update_cell(topic, "topic", marquee_window(name, self._topic_width, offset))

    @property
    def selected_topic(self) -> str | None:
        # cursor_row is -1 (Textual's "no valid row" sentinel, e.g. an
        # empty table) whenever a RowHighlighted fires with no rows left to
        # highlight -- not caught by the `>= len(...)` check below, and
        # `list[-1]` on an empty list raises IndexError rather than
        # returning something out of range.
        if self.cursor_row is None or self.cursor_row < 0 or self.cursor_row >= len(self._topic_order):
            return None
        return self._topic_order[self.cursor_row]

    @property
    def selected_report(self) -> TopicReport | None:
        topic = self.selected_topic
        return None if topic is None else self._reports_by_topic.get(topic)
