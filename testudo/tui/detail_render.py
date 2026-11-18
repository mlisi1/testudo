"""Detail Panel rendering: one `TopicReport` -> the markup shown for the highlighted topic.

Pure functions (a report and a pane size in, a markup string out), so the
layout is unit-testable without a running Textual app.

A plugin's `CheckStatus.values` is a flat `dict[str, str]`; this is the
generic renderer that makes that readable without any plugin having to
format for the TUI itself:

- a header line: severity badge, topic name, message type and tier;
- short `key: value` pairs as an aligned grid, flowed into as many columns
  as the pane has room for, so a status with many values (node profiling
  has 15+) fits on screen instead of scrolling one narrow column while
  most of the pane's width sits empty;
- keys humanized for display (`time_in_status_s: 12.4` -> `time in status
  12.4 s`) -- see `humanize_pairs`;
- a value that is long, or pre-formatted across several lines (a plugin's
  own meter block, TUI_DATA_DESIGN.md's Track A), keeps a full-width line
  of its own, in its original position;
- who publishes the topic, when known: leading the body for a topic no
  plugin reads (presence/vitals), last and only in a tall pane under a
  plugin's own readings;
- active error codes last.
"""
from __future__ import annotations

import math

from rich.text import Text

from testudo.core.fault_history import ClearedFault, format_ago, latest_per_code
from testudo.core.plain_values import strip_markup
from testudo.core.topic_report import TopicReport
from testudo.tui.detail_values import humanize_pairs, resolve_values, style_value
from testudo.plugins.base import (
    COMPACT_VALUE_SUFFIX,
    MORE_VALUE_SUFFIX,
    SEVERITY_COLORS,
    SEVERITY_ICONS,
    SEVERITY_LABELS,
)

#: Detail Panel width (columns) below which `resolve_values` prefers a
#: value's `_compact` variant over its full one, where both exist.
NARROW_DETAIL_WIDTH = 60

#: Rows under the header a Detail Panel needs before `_more` values are
#: shown at all -- the 136x36 case has ~13, the 101x23 one ~6.
TALL_DETAIL_ROWS = 10

#: A value wider than this never shares a grid row -- it gets its own line.
_MAX_GRID_VALUE_WIDTH = 28

#: Blank columns between two grid columns, and between a label and its value.
_COLUMN_GAP = 4
_LABEL_GAP = 2

#: How many of a grid's widest values `_fitted_body_lines` will move onto
#: full-width lines of their own while looking for a layout that fits.
_MAX_VALUES_MOVED_OUT_OF_GRID = 3

#: At most this many of the topic's cleared codes, under its active ones.
MAX_CLEARED_LINES = 3

#: Rows the grid aims to stay within when the pane's height isn't known.
_DEFAULT_GRID_ROWS = 6


def _width(markup: str) -> int:
    """Terminal cells `markup` occupies once rendered (tags excluded)."""
    return Text.from_markup(markup).cell_len


def _pad(markup: str, width: int) -> str:
    return markup + " " * max(0, width - _width(markup))


def _grid_lines(pairs: list[tuple[str, str]], width: int, max_rows: int) -> tuple[list[str], bool, int]:
    """Lay `pairs` out column-major in the fewest columns that keep them within `max_rows`.

    Fewest, not most: a handful of values reads best as one plain list,
    and only a status with more values than the pane has rows spreads
    sideways. Never more columns than `width` can hold -- past that the
    grid just runs longer. Returns the lines, whether they stayed within
    `max_rows` (False: the pane would have to scroll), and the first
    column's label width.
    """
    if not pairs:
        return [], True, 0
    columns = min(len(pairs), max(1, math.ceil(len(pairs) / max(1, max_rows))))
    while True:
        rows = math.ceil(len(pairs) / columns)
        chunks = [pairs[start : start + rows] for start in range(0, len(pairs), rows)]
        label_widths = [max(_width(label) for label, _ in chunk) for chunk in chunks]
        value_widths = [max(_width(value) for _, value in chunk) for chunk in chunks]
        total = sum(label_widths) + sum(value_widths) + _LABEL_GAP * len(chunks) + _COLUMN_GAP * (len(chunks) - 1)
        if total <= width or columns == 1:
            break
        columns -= 1

    lines = []
    for row in range(rows):
        cells = []
        for chunk, label_width, value_width in zip(chunks, label_widths, value_widths):
            if row >= len(chunk):
                continue
            label, value = chunk[row]
            cells.append(f"[dim]{_pad(label, label_width)}[/dim]{' ' * _LABEL_GAP}{_pad(value, value_width)}")
        lines.append((" " * _COLUMN_GAP).join(cells).rstrip())
    return lines, rows <= max_rows, label_widths[0]


def _body_lines(entries: list[tuple[str, str, bool]], width: int, max_rows: int) -> tuple[list[str], bool]:
    """Every `(label, value, own_line)` entry in order: runs of grid entries as grids, the rest full-width.

    Returns the lines and whether they fit in `max_rows` altogether.
    """
    own_line_rows = sum(value.count("\n") + 1 for _, value, own_line in entries if own_line)
    grid_rows = max(1, max_rows - own_line_rows)
    lines: list[str] = []
    fits = True
    pending: list[tuple[str, str]] = []
    # A full-width line right under a grid lines its label up with that
    # grid's first column, so the two read as one list.
    label_width = 0

    def flush() -> None:
        nonlocal fits, label_width
        if not pending:
            return
        grid, grid_fits, label_width = _grid_lines(pending, width, grid_rows)
        lines.extend(grid)
        fits = fits and grid_fits
        pending.clear()

    for label, value, own_line in entries:
        if not own_line:
            pending.append((label, value))
            continue
        flush()
        # A value with its own newlines is a pre-formatted, self-labeled
        # block (Track A) -- shown as-is, without a label of its own.
        if "\n" in value:
            lines.append(value)
            label_width = 0
        else:
            lines.append(f"[dim]{_pad(label, label_width)}[/dim]{' ' * _LABEL_GAP}{value}")
    flush()
    return lines, fits and len(lines) <= max_rows


def _fitted_body_lines(entries: list[tuple[str, str, bool]], width: int, max_rows: int) -> list[str]:
    """`_body_lines`, moving the widest grid values onto lines of their own if that makes it all fit.

    One wide value sets the width of its whole grid column, and can be
    the only reason a second or third column doesn't fit beside it --
    pushing every other value into a longer, scrolling list. Giving just
    that value a full-width line -- below the rest, so the grid stays
    in one piece -- costs one row and often frees a whole column. Tried
    a few values deep; if it never fits, the plain layout (nothing
    moved) is used, since it's the more regular of two layouts that
    both overflow.
    """
    plain, fits = _body_lines(entries, width, max_rows)
    attempt = list(entries)
    for _ in range(_MAX_VALUES_MOVED_OUT_OF_GRID):
        if fits:
            break
        in_grid = [index for index, (_, _, own_line) in enumerate(attempt) if not own_line]
        if len(in_grid) < 2:
            break
        widest = max(in_grid, key=lambda index: _width(attempt[index][1]))
        label, value, _ = attempt.pop(widest)
        attempt.append((label, value, True))
        lines, fits = _body_lines(attempt, width, max_rows)
        if fits:
            return lines
    return plain


def _publishers_entry(report: TopicReport) -> tuple[str, str, bool] | None:
    """A full-width `publishers` body entry for `report`, or None if its publishers aren't known."""
    if not report.publishers:
        return None
    label = "publisher" if len(report.publishers) == 1 else "publishers"
    return (label, ", ".join(report.publishers), True)


def _header_line(report: TopicReport, width: int) -> str:
    if report.tier == "excluded":
        # Nothing is checked on an excluded topic, so there is no severity
        # to badge -- same reasoning as its blank Topic Panel cells.
        title = f"[b]{report.topic}[/b]"
    else:
        severity = report.status.severity
        color = SEVERITY_COLORS.get(severity, "white")
        label = SEVERITY_LABELS.get(severity, str(severity))
        title = f"[{color}]{SEVERITY_ICONS.get(severity, '?')} {label}[/{color}]  [b]{report.topic}[/b]"
    line = f"{title}  [dim]{report.msg_type} · {report.tier} tier[/dim]"
    if _width(line) > width:
        # Too narrow for the full type: keep the message name, drop its package path.
        line = f"{title}  [dim]{report.msg_type.rsplit('/', 1)[-1]} · {report.tier}[/dim]"
    return line


def _body_entries(report: TopicReport, values: dict[str, str], width: int, is_tall: bool) -> list[tuple[str, str, bool]]:
    """`values` as (label, value, own_line) body entries, with the publishers entry placed for `report`'s tier."""
    pairs = humanize_pairs(resolve_values(values, width < NARROW_DETAIL_WIDTH, is_tall))
    entries = [
        (label, value, True) if "\n" in value or _width(value) > _MAX_GRID_VALUE_WIDTH else (label, style_value(value), False)
        for label, value in pairs
    ]
    publishers = _publishers_entry(report)
    if publishers is not None:
        # Without a plugin's readings, who publishes the topic is the most
        # useful thing to show, so it leads; under a plugin's readings it's
        # secondary -- shown last, and only with rows to spare.
        if report.tier != "full":
            entries.insert(0, publishers)
        elif is_tall:
            entries.append(publishers)
    return entries


def cleared_fault_line(fault: ClearedFault, times: int, now: float, width: int) -> str:
    """One dim row for a cleared code: `✗ [LOC-001] cleared 2m ago, lasted 5s ×3 · message`.

    The icon is the worst severity it reached; the message is the code's
    last, without its markup (it's history, not an alarm), cut to `width`.
    """
    color = SEVERITY_COLORS.get(fault.severity, "white")
    repeat = f" ×{times}" if times > 1 else ""
    text = (
        f"[{fault.code}] cleared {format_ago(now - fault.cleared_at)} ago, "
        f"lasted {format_ago(fault.lasted_s)}{repeat} · {strip_markup(fault.message)}"
    )
    room = max(1, width - 2)
    if len(text) > room:
        text = text[: room - 1] + "…"
    icon = SEVERITY_ICONS.get(fault.severity, "?")
    # Every "[" escaped, not `rich.markup.escape`: that one leaves `[LIVE-003]`
    # alone (it only escapes lowercase tag shapes), and Textual's own markup
    # parser then swallows it as a style tag.
    return f"[dim {color}]{icon}[/dim {color}] [dim]{text.replace('[', chr(92) + '[')}[/dim]"


def render_detail(
    report: TopicReport,
    width: int,
    height: int | None = None,
    cleared: tuple[ClearedFault, ...] | list[ClearedFault] = (),
    now: float = 0.0,
) -> str:
    """The Detail Panel's full markup for `report`, laid out for a `width` x `height` pane.

    `status.message` (the single rolled-up free-text summary) is
    deliberately not shown -- `status.codes` supersedes it: every
    currently active problem gets its own `[CODE] message` line,
    independently of the others, rather than one string with them all
    run together. `height=None` (unknown) falls back to a fixed row
    budget for the value grid, and leaves `_more` values out.

    `cleared` (this topic's cleared faults, newest first, aged against
    `now`) come last, one dim line per code, only in rows nothing else
    needs: history never pushes a current reading or code out of view.
    """
    lines = [_header_line(report, width)]
    # A header still wider than the pane wraps; those rows aren't the grid's to use.
    header_rows = max(1, math.ceil(_width(lines[0]) / max(1, width)))
    codes = report.status.codes
    code_lines = [f"[b]\\[{code}][/b] {message}" for code, message in codes.items()]
    # Rows left under the header once the codes have theirs -- a
    # code message longer than the pane is wide wraps onto more than one.
    code_rows = sum(math.ceil(_width(line) / max(1, width)) for line in code_lines)
    max_rows = _DEFAULT_GRID_ROWS if height is None else max(1, height - header_rows - code_rows)

    is_tall = height is not None and height - header_rows >= TALL_DETAIL_ROWS
    values = report.status.values
    # `_more` blocks are extras for a tall pane: drop them, last first,
    # until the body fits, so one big block (a TF tree) never pushes the
    # codes -- what's actually wrong -- below the fold.
    more_keys = [key for key in values if key.endswith(MORE_VALUE_SUFFIX)] if is_tall else []
    for keep in range(len(more_keys), -1, -1):
        dropped = {name for key in more_keys[keep:] for name in (key, key + COMPACT_VALUE_SUFFIX)}
        kept = {key: value for key, value in values.items() if key not in dropped}
        body = _fitted_body_lines(_body_entries(report, kept, width, is_tall), width, max_rows)
        # A multi-line value is one entry in `body` but several rows on screen.
        if sum(line.count("\n") + 1 for line in body) <= max_rows:
            break
    lines.extend(body)

    if codes:
        # A blank line sets the codes apart -- unless it's the one line
        # that would push the last of them out of view.
        if height is None or header_rows - 1 + len(lines) + 1 + code_rows <= height:
            lines.append("")
        lines.extend(code_lines)

    past = latest_per_code(cleared)[:MAX_CLEARED_LINES]
    if past:
        used = header_rows - 1 + sum(line.count("\n") + 1 for line in lines) + code_rows - len(code_lines)
        room = MAX_CLEARED_LINES + 1 if height is None else height - used
        if room >= 2:
            lines.append("")
            lines += [cleared_fault_line(fault, times, now, width) for fault, times in past[: room - 1]]
    return "\n".join(lines)
