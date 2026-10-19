"""Plain-text projection of a CheckStatus's values, for every output that isn't the TUI.

Plugins format `CheckStatus.values` for the Detail Panel: Rich markup for
color, and `_compact` narrow-terminal duplicates of wide values (see
`COMPACT_VALUE_SUFFIX`). A published DiagnosticStatus (read by
rqt_robot_monitor, Foxglove, ...) and the `check`/`replay` report need
the same data without either.
"""
from __future__ import annotations

from rich.errors import MarkupError
from rich.text import Text

from testudo.plugins.base import COMPACT_VALUE_SUFFIX


def strip_markup(value: str) -> str:
    """`value` with Rich markup tags removed, or unchanged if it isn't valid markup."""
    try:
        return Text.from_markup(value).plain
    except MarkupError:
        return value


def plain_values(values: dict[str, str], single_line: bool = False) -> dict[str, str]:
    """`values` without markup or `_compact` duplicates, in their original order.

    `single_line=True` also drops multi-line values (pre-formatted meter
    blocks, which only read as intended in the Detail Panel's grid) for a
    one-line-per-topic report.
    """
    plain: dict[str, str] = {}
    for key, value in values.items():
        if key.endswith(COMPACT_VALUE_SUFFIX):
            continue
        text = strip_markup(value)
        if single_line and "\n" in text:
            continue
        plain[key] = text
    return plain
