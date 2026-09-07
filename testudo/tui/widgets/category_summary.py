"""Summary view: one row per category, worst-status colored.

137 topics won't fit one screen; this is the entry point, with drill-down
into a category's individual topics handled by `topic_panel.py`.
"""
from __future__ import annotations

from testudo.core.topic_report import TopicReport
from testudo.plugins.base import SEVERITY_LABELS, Severity
from testudo.tui.categorize import category_for
from testudo.tui.keybinds import NavDataTable

SEVERITY_STYLE = {
    Severity.OK: "green",
    Severity.WARN: "yellow",
    Severity.ERROR: "red",
    Severity.STALE: "magenta",
}


class CategorySummaryTable(NavDataTable):
    """One row per category: name, worst severity, topic count."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.cursor_type = "row"
        self._category_order: list[str] = []
        self._filter_text = ""
        self._sort_by_severity = True

    def on_mount(self) -> None:
        self.add_columns("Category", "Status", "Topics")

    def set_filter(self, text: str) -> None:
        self._filter_text = text.strip().lower()

    def set_sort_by_severity(self, sort_by_severity: bool) -> None:
        self._sort_by_severity = sort_by_severity

    def update_categories(self, reports: list[TopicReport]) -> None:
        """Rebuild rows from `reports`, grouped by category, preserving the current selection."""
        previous_selection = self.selected_category

        grouped: dict[str, list[TopicReport]] = {}
        for report in reports:
            grouped.setdefault(category_for(report), []).append(report)

        rows = [
            (category, max(r.status.severity for r in category_reports), len(category_reports))
            for category, category_reports in grouped.items()
            if not self._filter_text or self._filter_text in category.lower()
        ]
        if self._sort_by_severity:
            rows.sort(key=lambda row: (-row[1], row[0]))
        else:
            rows.sort(key=lambda row: row[0])

        self._category_order = [row[0] for row in rows]
        self.clear()
        for category, worst, count in rows:
            label = SEVERITY_LABELS.get(worst, str(worst))
            style = SEVERITY_STYLE.get(worst, "white")
            self.add_row(category, f"[{style}]{label}[/{style}]", str(count), key=category)

        if previous_selection in self._category_order:
            self.move_cursor(row=self._category_order.index(previous_selection))

    @property
    def selected_category(self) -> str | None:
        if self.cursor_row is None or self.cursor_row >= len(self._category_order):
            return None
        return self._category_order[self.cursor_row]
