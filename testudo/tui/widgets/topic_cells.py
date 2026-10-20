"""Topic Panel cell formatting: pure functions from a report (or a name) to cell markup.

Kept apart from `topic_panel`'s widget so the formatting rules (status,
rate, type and topic-name shortening) are testable without a running app.
"""
from __future__ import annotations

from rich.text import Text

from testudo.core.discovery import ACTION_STATUS_SUFFIX
from testudo.core.topic_report import IDLE_LABEL, LATCHED_LABEL, TopicReport, quiet_label
from testudo.plugins.base import SEVERITY_COLORS, SEVERITY_ICONS, SEVERITY_LABELS, Severity

#: Status cells for an OK topic that is quiet by design, or never
#: subscribed to: a hollow glyph says "nothing to judge right now"
#: rather than a green "OK" implying content was actually checked.
_QUIET_STATUS_CELLS = {
    IDLE_LABEL: "[dim]◌ idle[/dim]",
    LATCHED_LABEL: "[green]● latched[/green]",
}
_PRESENCE_STATUS_CELL = "[dim]◌ present[/dim]"


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


#: Header markers for the column a table is currently sorted by (`s`
#: toggles between the two): severity is worst first, a name is A to Z.
SEVERITY_SORT_MARKER = " ▼"
NAME_SORT_MARKER = " ▲"


def sort_markers(sort_by_severity: bool) -> tuple[str, str]:
    """(name column marker, Status column marker): exactly one of them is set."""
    return ("", SEVERITY_SORT_MARKER) if sort_by_severity else (NAME_SORT_MARKER, "")


def topic_header(prefix: str, width: int, marker: str = "") -> str:
    """The Topic column's header: "Topic" (+ sort `marker`), plus the stripped common prefix (its tail, if it must be cut)."""
    title = f"Topic{marker}"
    if not prefix:
        return title
    label = f"{title}  {prefix}"
    if len(label) <= width:
        return label
    room = width - len(f"{title}  …")
    return f"{title}  …{prefix[-room:]}" if room > 0 else title


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


#: The leading glyph of nav2_goals' `state` headline chip -> the Nav2
#: Actions "State" cell: what the action server is doing now.
_ACTION_STATE_CELLS = {
    "▶": "[green]▶ run[/green]",
    "⊘": "[yellow]⊘ stop[/yellow]",
}
_ACTION_IDLE_CELL = "[dim]◌ idle[/dim]"

_ACTION_MSG_TYPE = "action_msgs/msg/GoalStatusArray"


def format_memory(memory_mb: str | None) -> str:
    """A node's RSS in at most five characters ("56.4M", "640M", "1.23G"), or "-" if unknown."""
    try:
        megabytes = float(memory_mb) if memory_mb is not None else None
    except ValueError:
        megabytes = None
    if megabytes is None:
        return "-"
    if megabytes < 100:
        return f"{megabytes:.1f}M"
    if megabytes < 1000:
        return f"{megabytes:.0f}M"
    return f"{megabytes / 1024:.2f}G"


def rate_column_header(report: TopicReport) -> str:
    """The third column's header for `report`'s category: "Hz" unless a rate means nothing there.

    A node has no publish rate, so its column shows memory ("Mem"); an
    action's status topic only publishes on transitions, so its column
    shows what the server is doing now ("State").
    """
    if report.tier == "node":
        return "Mem"
    if report.msg_type == _ACTION_MSG_TYPE and report.tier == "full":
        return "State"
    return "Hz"


def rate_cell(report: TopicReport, header: str) -> str:
    """The third column's cell for `report`, under `header` (see `rate_column_header`)."""
    if header == "Mem":
        # Node rows carry the Detail Panel's regrouped values
        # (`node_profiling.report.detail_panel_values`): "56.4 MB (0.18 %)".
        memory = report.status.values.get("memory", "")
        return format_memory(memory.split()[0] if memory else None)
    if header == "State":
        state = Text.from_markup(report.status.values.get("state", "")).plain
        return _ACTION_STATE_CELLS.get(state[:1], _ACTION_IDLE_CELL)
    # A latched topic's "rate" is just its burst of initial messages --
    # meaningless for something that publishes once.
    return format_rate(None if quiet_label(report.status) == LATCHED_LABEL else report.rate_hz)


def type_cell(msg_type: str, width: int) -> str:
    """`msg_type` in at most `width` cells: `pkg/Name` (package dimmed), else `Name`, else cut with `…`.

    The `/msg/` middle is dropped: every topic type has it, so it only
    costs width.
    """
    package, _, name = msg_type.rpartition("/")
    package = package.removesuffix("/msg")
    if package and len(package) + 1 + len(name) <= width:
        return f"[dim]{package}/[/dim]{name}"
    if len(name) <= width:
        return name
    return name[: max(0, width - 1)] + "…"


def display_name(topic: str, prefix: str) -> str:
    """`topic` as a Topic Panel row shows it: `prefix` stripped, and an action's status topic as the action."""
    name = topic[len(prefix) :]
    if name.endswith(ACTION_STATUS_SUFFIX) and len(name) > len(ACTION_STATUS_SUFFIX):
        return name[: -len(ACTION_STATUS_SUFFIX)]
    return name


def format_rate(rate_hz: float | None) -> str:
    """A rate in at most five characters ("9.87", "12.3", "28.9k"), or "-" when none is available yet."""
    if rate_hz is None:
        return "-"
    for bound, divisor, fmt in _RATE_FORMATS:
        if rate_hz < bound:
            return fmt.format(rate_hz / divisor)
    return f"{rate_hz / 1e6:.2f}M"
