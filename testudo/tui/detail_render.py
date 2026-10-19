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
- active error codes last.
"""
from __future__ import annotations

import math

from rich.text import Text

from testudo.core.topic_report import TopicReport
from testudo.plugins.base import SEVERITY_COLORS, SEVERITY_ICONS, SEVERITY_LABELS

#: `CheckStatus.values` key suffix marking a value's narrow-terminal
#: alternative -- e.g. a plugin emits both "gauge" (a wide multi-segment
#: meter) and "gauge_compact" (a single colored square) for the same
#: metric, and `resolve_values` below picks whichever fits the Detail
#: Panel's current width. A plain naming convention, not a CheckStatus
#: schema change (TUI_DATA_DESIGN.md's Track A) -- any plugin can opt in.
COMPACT_VALUE_SUFFIX = "_compact"

#: Detail Panel width (columns) below which `resolve_values` prefers a
#: value's `_compact` variant over its full one, where both exist.
NARROW_DETAIL_WIDTH = 60

#: Key suffix -> unit shown after the value instead of inside the label.
#: Longest suffix first, so "_mps" is tried before "_s" would ever match.
_UNIT_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("_per_sec", "/s"),
    ("_per_min", "/min"),
    ("_percent", "%"),
    ("_mps", "m/s"),
    ("_mb", "MB"),
    ("_hz", "Hz"),
    ("_s", "s"),
    ("_m", "m"),
)

#: Label words shown with their conventional capitalization rather than as written in the key.
_ACRONYMS = {"cpu": "CPU", "gpu": "GPU", "pid": "PID", "psd": "PSD", "fds": "FDs", "id": "ID", "tf": "TF"}

#: Values that mean "nothing to show here", dimmed so real readings stand out.
_PLACEHOLDER_VALUES = frozenset({"n/a", "off", "none", "warming up", "-"})

#: A value wider than this never shares a grid row -- it gets its own line.
_MAX_GRID_VALUE_WIDTH = 28

#: Blank columns between two grid columns, and between a label and its value.
_COLUMN_GAP = 4
_LABEL_GAP = 2

#: How many of a grid's widest values `_fitted_body_lines` will move onto
#: full-width lines of their own while looking for a layout that fits.
_MAX_VALUES_MOVED_OUT_OF_GRID = 3

#: Rows the grid aims to stay within when the pane's height isn't known.
_DEFAULT_GRID_ROWS = 6


def resolve_values(values: dict[str, str], is_narrow: bool) -> list[tuple[str, str]]:
    """Collapse `<key>`/`<key>_compact` pairs in `values` down to one entry each.

    Preserves `values`' insertion order. A key with no compact counterpart
    (most of them) passes through unchanged, from either side of the pair.
    """
    resolved: list[tuple[str, str]] = []
    skip: set[str] = set()
    for key, value in values.items():
        if key in skip:
            continue
        if key.endswith(COMPACT_VALUE_SUFFIX):
            base = key[: -len(COMPACT_VALUE_SUFFIX)]
            skip.add(base)
            if is_narrow:
                resolved.append((base, value))
            continue
        compact_key = f"{key}{COMPACT_VALUE_SUFFIX}"
        if compact_key in values:
            skip.add(compact_key)
            resolved.append((key, values[compact_key] if is_narrow else value))
            continue
        resolved.append((key, value))
    return resolved


def _label(key: str) -> str:
    return " ".join(_ACRONYMS.get(word, word) for word in key.split("_"))


def _is_number(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


def _split_unit(key: str) -> tuple[str, str]:
    """`("time_in_status", "s")` for `time_in_status_s`; `(key, "")` when no unit suffix applies."""
    for suffix, unit in _UNIT_SUFFIXES:
        if key.endswith(suffix) and len(key) > len(suffix):
            return key[: -len(suffix)], unit
    return key, ""


def humanize_pairs(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Display `(label, value)` for each `(key, value)`: spaces for underscores, units on the value.

    A key ending in a known unit suffix (`_s`, `_mb`, `_hz`, `_per_sec`,
    ...) drops it from the label and, when the value is a plain number,
    gains it on the value instead: `memory_mb: 640.0` -> `memory  640.0
    MB`. A non-numeric value ("n/a", "warming up") is left bare. Where
    dropping the suffix would leave two entries sharing one label
    (`memory_mb`/`memory_percent`, or a count next to its own
    `_per_sec` rate), the unit stays in the label instead -- `memory
    (MB)`, `memory (%)` -- so they remain distinguishable whatever their
    values are. Values are otherwise passed through untouched, Rich
    markup included.
    """
    stems = [_split_unit(key) for key, _ in pairs]
    label_uses: dict[str, int] = {}
    for stem, _unit in stems:
        label_uses[stem] = label_uses.get(stem, 0) + 1

    humanized = []
    for (key, value), (stem, unit) in zip(pairs, stems):
        label = _label(stem)
        if not unit:
            humanized.append((label, value))
        elif label_uses[stem] > 1:
            humanized.append((f"{label} ({unit})", value))
        else:
            humanized.append((label, f"{value} {unit}" if _is_number(value) else value))
    return humanized


def _style_value(value: str) -> str:
    if value == "":
        return "[dim]–[/dim]"
    if value.lower() in _PLACEHOLDER_VALUES:
        return f"[dim]{value}[/dim]"
    return value


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


def render_detail(report: TopicReport, width: int, height: int | None = None) -> str:
    """The Detail Panel's full markup for `report`, laid out for a `width` x `height` pane.

    `status.message` (the single rolled-up free-text summary) is
    deliberately not shown -- `status.codes` supersedes it: every
    currently active problem gets its own `[CODE] message` line,
    independently of the others, rather than one string with them all
    run together. `height=None` (unknown) falls back to a fixed row
    budget for the value grid.
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

    pairs = humanize_pairs(resolve_values(report.status.values, width < NARROW_DETAIL_WIDTH))
    entries = [
        (label, value, True) if "\n" in value or _width(value) > _MAX_GRID_VALUE_WIDTH else (label, _style_value(value), False)
        for label, value in pairs
    ]
    body = _fitted_body_lines(entries, width, max_rows)
    lines.extend(body)

    if codes:
        # A blank line sets the codes apart -- unless it's the one line
        # that would push the last of them out of view.
        if height is None or header_rows - 1 + len(lines) + 1 + code_rows <= height:
            lines.append("")
        lines.extend(code_lines)
    return "\n".join(lines)
