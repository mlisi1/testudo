"""Plugin Panel's "Needs attention" list: the active codes to look at first, worst first.

Fills the rows a short category list leaves empty under it, one line per
active code across every topic, like `✗ ODOM-001 …/gps` or
`■ LIVE-003 6 topics`. Part of the Plugin Panel, not a pane of its own,
and hidden when there's no room.

Rows the active codes leave over hold a dim "Cleared" list: codes that
were active and aren't any more, newest first, with how long ago
(`✗ GNSS-002 2m /gps`) -- so a fault that came and went between two
glances still shows it happened.
"""
from __future__ import annotations

from rich.markup import escape

from testudo.core.fault_history import ClearedFault, format_ago
from testudo.core.topic_report import TopicReport
from testudo.plugins.base import SEVERITY_COLORS, SEVERITY_ICONS, Severity, code_severity
from testudo.tui.widgets.topic_cells import tail_first

ATTENTION_TITLE = "Needs attention"

#: At most this many codes: past five, it's a second Topic Panel, not "what first".
MAX_ATTENTION_ENTRIES = 5

CLEARED_TITLE = "Cleared"

#: At most this many cleared codes: a reminder, not a log.
MAX_CLEARED_ENTRIES = 3


def attention_entries(reports: list[TopicReport]) -> list[tuple[int, str, list[str]]]:
    """(severity, code, topics) per active code on non-OK reports, worst first.

    One entry per code, not per topic: six silent topics sharing LIVE-003
    are one thing to look at, and listing each would push every other code
    off the list. A code takes its own severity (`code_severity`: a WARN
    code on a topic in ERROR stays WARN), the worst across its topics.
    """
    by_code: dict[str, tuple[int, list[str]]] = {}
    for report in reports:
        if report.status.severity <= Severity.OK:
            continue
        for code in report.status.codes:
            severity, topics = by_code.get(code, (Severity.OK, []))
            by_code[code] = (max(severity, code_severity(report.status, code)), topics + [report.topic])
    entries = [(severity, code, sorted(topics)) for code, (severity, topics) in by_code.items()]
    entries.sort(key=lambda entry: (-entry[0], -len(entry[2]), entry[1]))
    return entries


def problem_topics(reports: list[TopicReport]) -> list[str]:
    """Every topic with an active code, in the order the list shows them (`n`/`N` walk this).

    A topic appears once, under its first (worst) code -- the same order the
    "Needs attention" list reads top to bottom, extended past its five lines.
    """
    seen: dict[str, None] = {}
    for _severity, _code, topics in attention_entries(reports):
        for topic in topics:
            seen.setdefault(topic, None)
    return list(seen)


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


def cleared_entries(faults: tuple[ClearedFault, ...] | list[ClearedFault]) -> list[tuple[int, str, list[str], float]]:
    """(worst severity, code, topics, last cleared_at) per code, most recently cleared first."""
    by_code: dict[str, tuple[int, list[str], float]] = {}
    for fault in faults:
        severity, topics, cleared_at = by_code.get(fault.code, (Severity.OK, [], fault.cleared_at))
        if fault.topic not in topics:
            topics = topics + [fault.topic]
        by_code[fault.code] = (max(severity, fault.severity), topics, max(cleared_at, fault.cleared_at))
    entries = [(severity, code, sorted(topics), cleared_at) for code, (severity, topics, cleared_at) in by_code.items()]
    entries.sort(key=lambda entry: -entry[3])
    return entries


def cleared_line(severity: int, code: str, topics: list[str], ago_s: float, width: int) -> str:
    """`✗ CODE 2m topic` in `width` cells, all dim: the icon of the worst severity it reached."""
    color = SEVERITY_COLORS[severity]
    lead = f"{SEVERITY_ICONS[severity]} {code} {format_ago(ago_s)} "
    room = width - len(lead)
    if room <= 0:
        return f"[dim {color}]{escape(lead[:width])}[/dim {color}]"
    if len(topics) > 1:
        tail = f"{len(topics)} topics"
    else:
        tail = tail_first(topics[0], room) or "…" + topics[0][-(room - 1):]
    return f"[dim {color}]{escape(lead)}[/dim {color}][dim]{escape(tail)}[/dim]"


def attention_text(
    reports: list[TopicReport],
    width: int,
    rows: int,
    cleared: tuple[ClearedFault, ...] | list[ClearedFault] = (),
    now: float = 0.0,
) -> str | None:
    """The list as markup for `rows` free rows (titles included), or None when it shouldn't show.

    Active codes first; rows they leave over go to the codes in `cleared`,
    aged against `now`. None when there's nothing to show, or no room for
    a title and at least one entry -- the Plugin Panel then just leaves the
    space empty.
    """
    lines: list[str] = []
    entries = attention_entries(reports)
    limit = min(MAX_ATTENTION_ENTRIES, rows - 1)
    if entries and limit >= 1:
        lines.append(f"[bold]{ATTENTION_TITLE}[/bold]")
        lines += [attention_line(severity, code, topics, width) for severity, code, topics in entries[:limit]]
        hidden = len(entries) - limit
        if hidden > 0 and limit < rows - 1:
            lines.append(f"[dim]+{hidden} more[/dim]")

    past = cleared_entries(cleared)
    # A blank line between the two lists, when there is a first one.
    gap = 1 if lines else 0
    cleared_limit = min(MAX_CLEARED_ENTRIES, rows - len(lines) - gap - 1)
    if past and cleared_limit >= 1:
        lines += [""] * gap + [f"[bold dim]{CLEARED_TITLE}[/bold dim]"]
        lines += [
            cleared_line(severity, code, topics, now - cleared_at, width)
            for severity, code, topics, cleared_at in past[:cleared_limit]
        ]
    return "\n".join(lines) if lines else None
