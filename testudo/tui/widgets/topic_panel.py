"""Topic Panel: every topic within one selected category."""
from __future__ import annotations

from testudo.core.topic_report import TopicReport
from testudo.plugins.base import SEVERITY_COLORS, SEVERITY_LABELS
from testudo.tui.keybinds import NavDataTable

#: Text capacity of the "Topic" column. A name longer than this scrolls
#: back and forth (see `_advance_marquee`) rather than being hard-clipped
#: or growing the column to fit -- keeping this narrow is what leaves room
#: for Status/Hz without a horizontal scrollbar.
TOPIC_COLUMN_WIDTH = 16

#: Width of the optional plugin-defined extra column (`CheckStatus.
#: topic_panel_column`/`topic_panel_value`) -- short by design, same budget
#: class as Status/Hz, never a sentence.
EXTRA_COLUMN_WIDTH = 10

#: The extra column's fixed DataTable key. Its *label* changes per category
#: (whichever plugin currently owns the visible topics), so it's removed and
#: re-added with a new label rather than renamed in place.
EXTRA_COLUMN_KEY = "extra"

#: How often the marquee shifts by one character.
MARQUEE_INTERVAL_SECONDS = 0.4


def marquee_window(text: str, width: int, offset: int) -> str:
    """The `width`-wide slice of `text` starting at `offset`, or `text` unchanged if it already fits."""
    if len(text) <= width:
        return text
    return text[offset : offset + width]


def next_marquee_offset(text_length: int, width: int, offset: int, direction: int) -> tuple[int, int]:
    """One ping-pong step: advance `offset` by `direction`, bouncing at either end."""
    max_offset = text_length - width
    offset += direction
    if offset >= max_offset:
        return max_offset, -1
    if offset <= 0:
        return 0, 1
    return offset, direction


def format_rate(rate_hz: float | None) -> str:
    """"12.3" Hz, or "-" when no rate is available yet (e.g. topic just appeared)."""
    return f"{rate_hz:.1f}" if rate_hz is not None else "-"


class TopicDetailTable(NavDataTable):
    """One row per topic: name, status, publish rate, plus an optional
    plugin-defined extra column.

    No "tier" column here -- it's shown in the detail pane's header line
    instead (`/topic  (msg_type, tier tier)`), freeing that width for
    Topic/Status/Hz, the fields that matter for scanning a list. The full
    status message is only shown in the detail pane below, not here.

    Hz comes straight from `TopicVitals.rate_hz()`, which is already
    updated on every message arrival for every subscribed topic regardless
    of tier or content-check decimation -- displaying it here is free, not
    an extra measurement.

    A 4th column appears when the visible category's reports carry a
    `CheckStatus.topic_panel_column` (e.g. odometry's "Cov"), and disappears
    again for a category that doesn't (e.g. "Other Topics") -- see
    `_extra_column_header_for`/`_set_extra_column`. A topic currently
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

    def on_mount(self) -> None:
        # Fixed widths, not auto (content-fit): an auto "Topic" column grows
        # to fit the single longest topic name it's ever shown, which was
        # free to dominate the row and push the other columns off-screen
        # (needing a horizontal scroll to see them at all). The full,
        # untruncated message is always available in the detail pane below.
        self.add_column("Topic", key="topic", width=TOPIC_COLUMN_WIDTH)
        self.add_column("Status", key="status", width=8)
        self.add_column("Hz", key="rate", width=6)
        self.set_interval(MARQUEE_INTERVAL_SECONDS, self._advance_marquee)

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
        if extra_header != self._extra_column_header:
            self._set_extra_column(extra_header)
            self._needs_rebuild = True

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
        for report in reports:
            if report.status.topic_panel_column:
                return report.status.topic_panel_column
        return None

    def _set_extra_column(self, header: str | None) -> None:
        """Add/remove/relabel the extra column to match `header` (None = no extra column)."""
        if self._extra_column_header is not None:
            self.remove_column(EXTRA_COLUMN_KEY)
        if header is not None:
            self.add_column(header, key=EXTRA_COLUMN_KEY, width=EXTRA_COLUMN_WIDTH)
        self._extra_column_header = header

    def _rebuild(self, visible: dict[str, TopicReport]) -> None:
        previous_selection = self.selected_topic
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

    def _write_row(self, report: TopicReport) -> None:
        label = SEVERITY_LABELS.get(report.status.severity, str(report.status.severity))
        style = SEVERITY_COLORS.get(report.status.severity, "white")
        offset = self._marquee_offset.get(report.topic, 0)
        self.update_cell(report.topic, "topic", marquee_window(report.topic, TOPIC_COLUMN_WIDTH, offset))
        self.update_cell(report.topic, "status", f"[{style}]{label}[/{style}]")
        self.update_cell(report.topic, "rate", format_rate(report.rate_hz))
        if self._extra_column_header is not None:
            self.update_cell(report.topic, EXTRA_COLUMN_KEY, report.status.topic_panel_value or "-")

    def _advance_marquee(self) -> None:
        for topic in self._topic_order:
            if len(topic) <= TOPIC_COLUMN_WIDTH:
                continue
            offset, direction = next_marquee_offset(
                len(topic),
                TOPIC_COLUMN_WIDTH,
                self._marquee_offset.get(topic, 0),
                self._marquee_direction.get(topic, 1),
            )
            self._marquee_offset[topic] = offset
            self._marquee_direction[topic] = direction
            self.update_cell(topic, "topic", marquee_window(topic, TOPIC_COLUMN_WIDTH, offset))

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
