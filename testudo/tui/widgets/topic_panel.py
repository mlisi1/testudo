"""Topic Panel: every topic within one selected category."""
from __future__ import annotations

from testudo.core.topic_report import IDLE_LABEL, LATCHED_LABEL, TopicReport, quiet_label
from testudo.plugins.base import SEVERITY_COLORS, SEVERITY_ICONS, SEVERITY_LABELS, Severity
from testudo.tui.keybinds import NavDataTable

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

#: Status cells for an OK topic that is quiet by design, or never
#: subscribed to: a hollow glyph says "nothing to judge right now"
#: rather than a green "OK" implying content was actually checked.
_QUIET_STATUS_CELLS = {
    IDLE_LABEL: "[dim]◌ idle[/dim]",
    LATCHED_LABEL: "[green]● latched[/green]",
}
_PRESENCE_STATUS_CELL = "[dim]◌ present[/dim]"
RATE_COLUMN_WIDTH = 6

#: Columns reserved for the vertical scrollbar whether or not it's showing,
#: so the Topic column doesn't change width as rows come and go.
_SCROLLBAR_WIDTH = 2

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


def status_cell(report: TopicReport) -> str:
    """The Status column's markup for `report`: severity icon + label, or a quiet/presence glyph."""
    if report.tier == "presence" and report.status.severity == Severity.OK:
        return _PRESENCE_STATUS_CELL
    quiet = quiet_label(report.status)
    if quiet is not None:
        return _QUIET_STATUS_CELLS[quiet]
    label = SEVERITY_LABELS.get(report.status.severity, str(report.status.severity))
    style = SEVERITY_COLORS.get(report.status.severity, "white")
    icon = SEVERITY_ICONS.get(report.status.severity, "?")
    return f"[{style}]{icon} {label}[/{style}]"


def common_topic_prefix(topics: list[str]) -> str:
    """The namespace every one of `topics` shares, ending in "/" -- or "" if there's none worth stripping.

    Only a whole-namespace prefix counts (`/front_camera/camera/` for
    `/front_camera/camera/color/image_raw` and `.../depth/image_raw`, never
    a partial segment like `/front_camera/camera/co`), it needs at least
    two topics to be "common", and it never swallows a topic whole.
    """
    if len(topics) < 2:
        return ""
    shared = topics[0]
    for topic in topics[1:]:
        while not topic.startswith(shared):
            shared = shared[:-1]
    prefix = shared[: shared.rfind("/") + 1]
    if len(prefix) <= 1 or any(len(topic) == len(prefix) for topic in topics):
        return ""
    return prefix


def tail_first(name: str, width: int) -> str | None:
    """`name` if it fits in `width`, else "…/" plus as many trailing path segments as fit.

    The end of a topic name is the part that tells topics apart
    (`.../image_raw` vs. `.../image_raw/zstd`), so it's what gets kept.
    None if not even the last segment fits -- the caller scrolls the full
    name instead.
    """
    if len(name) <= width:
        return name
    segments = name.split("/")
    for start in range(1, len(segments)):
        tail = "…/" + "/".join(segments[start:])
        if len(tail) <= width:
            return tail
    return None


def topic_header(prefix: str, width: int) -> str:
    """The Topic column's header: "Topic", plus the stripped common prefix (its tail, if it must be cut)."""
    if not prefix:
        return "Topic"
    label = f"Topic  {prefix}"
    if len(label) <= width:
        return label
    room = width - len("Topic  …")
    return f"Topic  …{prefix[-room:]}" if room > 0 else "Topic"


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


#: (upper bound, divisor, format) for `format_rate`, first match wins.
#: Three significant digits throughout, so no rate is ever wider than five
#: characters -- a burst (two messages microseconds apart) can estimate a
#: rate in the tens of thousands, which "{:.1f}" would print as "28926.2".
_RATE_FORMATS = (
    (10.0, 1.0, "{:.2f}"),
    (100.0, 1.0, "{:.1f}"),
    (1e3, 1.0, "{:.0f}"),
    (1e4, 1e3, "{:.2f}k"),
    (1e5, 1e3, "{:.1f}k"),
    (1e6, 1e3, "{:.0f}k"),
)


def format_rate(rate_hz: float | None) -> str:
    """A rate in at most five characters ("9.87", "12.3", "28.9k"), or "-" when none is available yet."""
    if rate_hz is None:
        return "-"
    for bound, divisor, fmt in _RATE_FORMATS:
        if rate_hz < bound:
            return fmt.format(rate_hz / divisor)
    return f"{rate_hz / 1e6:.2f}M"


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
    an extra measurement.

    A 4th column appears when the visible category's reports carry a
    `CheckStatus.topic_panel_column` (e.g. odometry's "Cov"), and disappears
    again for a category that doesn't (e.g. "Other Topics") -- see
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
        self._topic_width = TOPIC_COLUMN_WIDTH
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
        self.add_column("Hz", key="rate", width=RATE_COLUMN_WIDTH)
        if self._extra_column_header is not None:
            self.add_column(self._extra_column_header, key=EXTRA_COLUMN_KEY, width=EXTRA_COLUMN_WIDTH)

    def _topic_width_for(self, pane_width: int) -> int:
        """The Topic column's text capacity in a `pane_width`-wide table: all the width the others don't use."""
        other_widths = [STATUS_COLUMN_WIDTH, RATE_COLUMN_WIDTH]
        if self._extra_column_header is not None:
            other_widths.append(EXTRA_COLUMN_WIDTH)
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
        if topic_width == self._topic_width and not columns_changed:
            return
        self._topic_width = topic_width
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
        prefix = common_topic_prefix(sorted(visible))
        if extra_header != self._extra_column_header or prefix != self._topic_prefix:
            self._extra_column_header = extra_header
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
        for report in reports:
            if report.status.topic_panel_column:
                return report.status.topic_panel_column
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
        return topic[len(self._topic_prefix) :]

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
        # A latched topic's "rate" is just its burst of initial messages --
        # meaningless for something that publishes once.
        rate = None if quiet_label(report.status) == LATCHED_LABEL else report.rate_hz
        self.update_cell(report.topic, "rate", format_rate(rate))
        if self._extra_column_header is not None:
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
