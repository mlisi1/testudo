"""Drill-down view: every topic within one selected category."""
from __future__ import annotations

from testudo.core.topic_report import TopicReport
from testudo.plugins.base import SEVERITY_LABELS
from testudo.tui.keybinds import NavDataTable
from testudo.tui.widgets.category_summary import SEVERITY_STYLE


class TopicDetailTable(NavDataTable):
    """One row per topic: name, tier, status, message."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.cursor_type = "row"
        self._topic_order: list[str] = []
        self._reports_by_topic: dict[str, TopicReport] = {}
        self._filter_text = ""
        self._sort_by_severity = True

    def on_mount(self) -> None:
        self.add_columns("Topic", "Tier", "Status", "Message")

    def set_filter(self, text: str) -> None:
        self._filter_text = text.strip().lower()

    def set_sort_by_severity(self, sort_by_severity: bool) -> None:
        self._sort_by_severity = sort_by_severity

    def update_topics(self, reports: list[TopicReport]) -> None:
        """Rebuild rows from `reports` (already filtered to one category), preserving selection."""
        previous_selection = self.selected_topic

        rows = [r for r in reports if not self._filter_text or self._filter_text in r.topic.lower()]
        if self._sort_by_severity:
            rows.sort(key=lambda r: (-r.status.severity, r.topic))
        else:
            rows.sort(key=lambda r: r.topic)

        self._topic_order = [r.topic for r in rows]
        self._reports_by_topic = {r.topic: r for r in rows}
        self.clear()
        for report in rows:
            label = SEVERITY_LABELS.get(report.status.severity, str(report.status.severity))
            style = SEVERITY_STYLE.get(report.status.severity, "white")
            self.add_row(
                report.topic,
                report.tier,
                f"[{style}]{label}[/{style}]",
                report.status.message,
                key=report.topic,
            )

        if previous_selection in self._topic_order:
            self.move_cursor(row=self._topic_order.index(previous_selection))

    @property
    def selected_topic(self) -> str | None:
        if self.cursor_row is None or self.cursor_row >= len(self._topic_order):
            return None
        return self._topic_order[self.cursor_row]

    @property
    def selected_report(self) -> TopicReport | None:
        topic = self.selected_topic
        return None if topic is None else self._reports_by_topic.get(topic)
