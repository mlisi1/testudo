"""Plugin Panel: one row per category (~ one row per plugin/check domain),
worst-status colored, with a compact per-severity breakdown.

137 topics won't fit one screen; this is the entry point, with drill-down
into a category's individual topics handled by the Topic Panel (`topic_panel.py`).
"""
from __future__ import annotations

from testudo.core.topic_report import TopicReport
from testudo.plugins.base import SEVERITY_COLORS, SEVERITY_ICONS, Severity
from testudo.tui.categorize import OTHER_CATEGORY, category_for
from testudo.tui.keybinds import NavDataTable

#: Worst-first, so a mixed row reads left-to-right in order of urgency.
_SEVERITY_DISPLAY_ORDER = (Severity.STALE, Severity.ERROR, Severity.WARN, Severity.OK)


def _severity_counts(reports: list[TopicReport]) -> dict[int, int]:
    counts: dict[int, int] = {}
    for report in reports:
        counts[report.status.severity] = counts.get(report.status.severity, 0) + 1
    return counts


def format_severity_breakdown(counts: dict[int, int]) -> str:
    """"[red]✗2[/red] [yellow]▲1[/yellow]" -- one segment per severity actually present, worst first."""
    segments = []
    for severity in _SEVERITY_DISPLAY_ORDER:
        count = counts.get(severity, 0)
        if count > 0:
            color = SEVERITY_COLORS[severity]
            segments.append(f"[{color}]{SEVERITY_ICONS[severity]}{count}[/{color}]")
    return " ".join(segments) if segments else "-"


class CategorySummaryTable(NavDataTable):
    """One row per category: name, and a per-severity breakdown in place of
    separate "worst status" and "topic count" columns -- more information
    in less width, since a full ERROR/WARN label per row costs more room
    than a small icon does.

    "Other Topics" (vitals-tier topics with no plugin identity to group by)
    always sorts last, in either sort mode -- it's the catch-all bucket,
    not a specific check worth competing for the top slot.

    `DataTable.clear()` resets both scroll position and cursor to (0, 0) --
    calling it on every poll would snap a scrolled-down view back to the
    top and jump it back on every refresh, which reads as constant
    flicker. So a routine update (same set of categories, values changed)
    only rewrites cell values in place; a full clear-and-rebuild only
    happens when the visible row *set* actually changes (added, removed,
    or the user just toggled sort order).
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.cursor_type = "row"
        self._category_order: list[str] = []
        self._filter_text = ""
        self._sort_by_severity = True
        self._needs_rebuild = True

    def on_mount(self) -> None:
        # Fixed widths, not auto (content-fit): an auto column grows to fit
        # its single widest cell ever seen, so one long category name would
        # otherwise be free to push the others -- or the whole table --
        # wider than the pane, forcing a horizontal scroll to see the rest.
        self.add_column("Category", key="category", width=14)
        self.add_column("Status", key="status", width=12)

    @property
    def sort_by_severity(self) -> bool:
        return self._sort_by_severity

    def set_filter(self, text: str) -> None:
        self._filter_text = text.strip().lower()

    def set_sort_by_severity(self, sort_by_severity: bool) -> None:
        if sort_by_severity != self._sort_by_severity:
            self._sort_by_severity = sort_by_severity
            self._needs_rebuild = True

    def update_categories(self, reports: list[TopicReport]) -> None:
        """Refresh from `reports`, grouped by category, preserving scroll/selection where possible."""
        grouped: dict[str, list[TopicReport]] = {}
        for report in reports:
            grouped.setdefault(category_for(report), []).append(report)

        visible = {
            category: _severity_counts(category_reports)
            for category, category_reports in grouped.items()
            if not self._filter_text or self._filter_text in category.lower()
        }

        if self._needs_rebuild or set(self._category_order) != set(visible):
            self._rebuild(visible)
            return

        for category in self._category_order:
            self._write_row(category, visible[category])

    def _sorted_categories(self, visible: dict[str, dict[int, int]]) -> list[str]:
        regular = [c for c in visible if c != OTHER_CATEGORY]
        if self._sort_by_severity:
            regular.sort(key=lambda c: (-max(visible[c]), c))
        else:
            regular.sort()
        return regular + [OTHER_CATEGORY] if OTHER_CATEGORY in visible else regular

    def _rebuild(self, visible: dict[str, dict[int, int]]) -> None:
        previous_selection = self.selected_category
        self._category_order = self._sorted_categories(visible)

        self.clear()
        for category in self._category_order:
            self.add_row(category, "", key=category)
            self._write_row(category, visible[category])

        if previous_selection in self._category_order:
            self.move_cursor(row=self._category_order.index(previous_selection))
        self._needs_rebuild = False

    def _write_row(self, category: str, counts: dict[int, int]) -> None:
        self.update_cell(category, "category", category)
        self.update_cell(category, "status", format_severity_breakdown(counts))

    @property
    def selected_category(self) -> str | None:
        # cursor_row is -1 (Textual's "no valid row" sentinel, e.g. an
        # empty table) whenever a RowHighlighted fires with no rows left to
        # highlight -- not caught by the `>= len(...)` check below, and
        # `list[-1]` on an empty list raises IndexError rather than
        # returning something out of range.
        if self.cursor_row is None or self.cursor_row < 0 or self.cursor_row >= len(self._category_order):
            return None
        return self._category_order[self.cursor_row]
