"""Plugin Panel's "Needs attention" list: the active codes to look at first, worst first.

Fills the rows a short category list leaves empty under it, one line per
active code across every topic, like `✗ ODOM-001 …/gps` or
`■ LIVE-003 6 topics`. Part of the Plugin Panel, not a pane of its own,
and hidden when there's no room.
"""
from __future__ import annotations

from rich.markup import escape

from testudo.core.topic_report import TopicReport
from testudo.plugins.base import SEVERITY_COLORS, SEVERITY_ICONS, Severity
from testudo.tui.widgets.topic_panel import tail_first

ATTENTION_TITLE = "Needs attention"

#: At most this many codes: past five, it's a second Topic Panel, not "what first".
MAX_ATTENTION_ENTRIES = 5


def attention_entries(reports: list[TopicReport]) -> list[tuple[int, str, list[str]]]:
    """(severity, code, topics) per active code on non-OK reports, worst first.

    One entry per code, not per topic: six silent topics sharing LIVE-003
    are one thing to look at, and listing each would push every other code
    off the list. A code takes its worst report's severity -- a code
    doesn't carry a severity of its own beyond its message's color, and the
    report's is what the Topic Panel shows next to the same topic anyway.
    """
    by_code: dict[str, tuple[int, list[str]]] = {}
    for report in reports:
        if report.status.severity <= Severity.OK:
            continue
        for code in report.status.codes:
            severity, topics = by_code.get(code, (Severity.OK, []))
            by_code[code] = (max(severity, report.status.severity), topics + [report.topic])
    entries = [(severity, code, sorted(topics)) for code, (severity, topics) in by_code.items()]
    entries.sort(key=lambda entry: (-entry[0], -len(entry[2]), entry[1]))
    return entries


def attention_line(severity: int, code: str, topics: list[str], width: int) -> str:
    """`✗ CODE topic` in `width` cells: icon and code in the severity's color, then the
    topic tail-first, or how many topics share the code when it's more than one."""
    color = SEVERITY_COLORS[severity]
    lead = f"{SEVERITY_ICONS[severity]} {code} "
    room = width - len(lead)
    if room <= 0:
        return f"[{color}]{escape(lead[:width])}[/{color}]"
    if len(topics) > 1:
        return f"[{color}]{escape(lead)}[/{color}][dim]{len(topics)} topics[/dim]"
    topic = topics[0]
    name = tail_first(topic, room) or "…" + topic[-(room - 1):]
    return f"[{color}]{escape(lead)}[/{color}][dim]{escape(name)}[/dim]"


def attention_text(reports: list[TopicReport], width: int, rows: int) -> str | None:
    """The list as markup for `rows` free rows (title included), or None when it shouldn't show.

    None when nothing needs attention, or there's no room for the title and
    at least one entry -- the Plugin Panel then just leaves the space empty.
    """
    entries = attention_entries(reports)
    limit = min(MAX_ATTENTION_ENTRIES, rows - 1)
    if not entries or limit < 1:
        return None
    lines = [f"[bold]{ATTENTION_TITLE}[/bold]"]
    lines += [attention_line(severity, code, topics, width) for severity, code, topics in entries[:limit]]
    hidden = len(entries) - limit
    if hidden > 0 and limit < rows - 1:
        lines.append(f"[dim]+{hidden} more[/dim]")
    return "\n".join(lines)
