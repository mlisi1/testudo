"""Plain-text projection of a CheckStatus's values, for every output that isn't the TUI.

A published DiagnosticStatus takes a plugin's `metrics` (raw numbers) when
it has any, and only falls back to `values` otherwise (`published_values`).

Plugins format `CheckStatus.values` for the Detail Panel: Rich markup for
color, and `_compact` narrow-terminal duplicates of wide values (see
`COMPACT_VALUE_SUFFIX`). A published DiagnosticStatus (read by
rqt_robot_monitor, Foxglove, ...) and the `check`/`replay` report need
the same data without either.
"""
from __future__ import annotations

from rich.errors import MarkupError
from rich.text import Text

from testudo.plugins.base import COMPACT_VALUE_SUFFIX, MORE_VALUE_SUFFIX, CheckStatus


def strip_markup(value: str) -> str:
    """`value` with Rich markup tags removed, or unchanged if it isn't valid markup."""
    try:
        return Text.from_markup(value).plain
    except MarkupError:
        return value


def plain_values(values: dict[str, str], single_line: bool = False) -> dict[str, str]:
    """`values` without markup or `_compact` duplicates, in their original order.

    `single_line=True` also drops multi-line values (pre-formatted meter
    blocks, which only read as intended in the Detail Panel's grid) and
    secondary `_more` values for a one-line-per-topic report.
    """
    plain: dict[str, str] = {}
    for key, value in values.items():
        if key.endswith(COMPACT_VALUE_SUFFIX):
            continue
        if single_line and key.endswith(MORE_VALUE_SUFFIX):
            continue
        text = strip_markup(value)
        if single_line and "\n" in text:
            continue
        plain[key] = text
    return plain


def format_metric(value: float | str) -> str:
    """A metric as DiagnosticStatus text: a number parseable as-is (`0.5`, `12`, `1.23e-05`), or the string."""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        # 10 significant digits: a latitude keeps ~1 mm, without float repr noise.
        return f"{value:.10g}"
    return value


def published_values(status: CheckStatus) -> dict[str, str]:
    """What a published DiagnosticStatus carries for `status`: its metrics, else its plain single-line values."""
    if status.metrics:
        return {key: format_metric(value) for key, value in status.metrics.items()}
    return plain_values(status.values, single_line=True)
