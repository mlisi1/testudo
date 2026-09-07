"""Drill-down view: every topic within one selected category."""
from __future__ import annotations

from testudo.core.topic_report import TopicReport
from testudo.plugins.base import SEVERITY_COLORS, SEVERITY_LABELS
from testudo.tui.keybinds import NavDataTable

#: Text capacity of the "Topic" column. A name longer than this scrolls
#: back and forth (see `_advance_marquee`) rather than being hard-clipped
#: or growing the column to fit -- keeping this narrow is what leaves room
#: for Status/Message without a horizontal scrollbar.
TOPIC_COLUMN_WIDTH = 16

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


class TopicDetailTable(NavDataTable):
    """One row per topic: name, status, message.

    No "tier" column here -- it's shown in the detail pane's header line
    instead (`/topic  (msg_type, tier tier)`), freeing that width for
    Topic/Status/Message, the three fields that matter for scanning a list.

    See `CategorySummaryTable`'s docstring for why routine updates avoid
    `clear()` (it resets scroll position and cursor, reading as flicker on
    a scrolled, actively-updating table): a full rebuild only happens when
    the visible topic *set* changes or sort order was just toggled.
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

    def on_mount(self) -> None:
        # Fixed widths, not auto (content-fit): an auto "Topic" column grows
        # to fit the single longest topic name it's ever shown, which was
        # free to dominate the row and push Status/Message off-screen
        # (needing a horizontal scroll to see them at all). The full,
        # untruncated message is always available in the detail pane below.
        self.add_column("Topic", key="topic", width=TOPIC_COLUMN_WIDTH)
        self.add_column("Status", key="status", width=8)
        self.add_column("Message", key="message", width=32)
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

        if self._needs_rebuild or set(self._topic_order) != set(visible):
            self._rebuild(visible)
            return

        self._reports_by_topic = visible
        for topic in self._topic_order:
            self._write_row(visible[topic])

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
        for topic in self._topic_order:
            self.add_row("", "", "", key=topic)
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
        self.update_cell(report.topic, "message", report.status.message)

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
        if self.cursor_row is None or self.cursor_row >= len(self._topic_order):
            return None
        return self._topic_order[self.cursor_row]

    @property
    def selected_report(self) -> TopicReport | None:
        topic = self.selected_topic
        return None if topic is None else self._reports_by_topic.get(topic)
