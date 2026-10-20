"""The `check`/`replay` text report: one line per topic plus the overall verdict.

Read by a person, so it uses `CheckStatus.values` (markup stripped, single
line), not the `metrics` that get published.
"""
from __future__ import annotations

import logging

from rich.console import Console
from rich.text import Text

from testudo.core.plain_values import plain_values, strip_markup
from testudo.core.topic_report import TopicReport
from testudo.plugins.base import SEVERITY_COLORS, SEVERITY_LABELS, CheckStatus, Severity

_logger = logging.getLogger("testudo")


def print_report(reports: list[TopicReport], overall: CheckStatus) -> int:
    """Print one line per topic plus the aggregated summary; return the exit code it implies.

    Exit codes follow the common CI convention: 0 OK, 1 WARN, 2 ERROR/STALE.
    Which severity drives `overall` (worst vs. weighted) is decided by
    `SubscriptionManager.overall_status` per `config.severity_mode` --
    printing/exit-coding here just reads the result, so both always agree
    with what got published to `/diagnostics`. Colored via `rich.Text`
    (content is always literal there, never markup, so a status message
    that happens to contain square brackets can't be misread as
    formatting); `Console` auto-disables color when stdout isn't a terminal.
    """
    console = Console()
    if not reports:
        _logger.info("no topics discovered")
        return 0

    for report in sorted(reports, key=lambda r: (-r.status.severity, r.topic)):
        console.print(_topic_line(report))

    console.print()
    console.print(_status_line(overall))

    if overall.severity == Severity.OK:
        return 0
    if overall.severity == Severity.WARN:
        return 1
    return 2


def _topic_line(report: TopicReport) -> Text:
    color = SEVERITY_COLORS.get(report.status.severity, "white")
    label = SEVERITY_LABELS.get(report.status.severity, str(report.status.severity))
    suffix = _values_suffix(report.status)

    line = Text()
    line.append(f"{label:5s} ", style=color)
    line.append(f"{report.topic:30s} ")
    line.append(f"[{report.tier:8s}] ", style="dim")
    line.append(f"{report.msg_type:35s} ")
    line.append(f"{strip_markup(report.status.message)}{suffix}")
    return line


def _status_line(status: CheckStatus) -> Text:
    color = SEVERITY_COLORS.get(status.severity, "white")
    label = SEVERITY_LABELS.get(status.severity, str(status.severity))
    suffix = _values_suffix(status)

    line = Text()
    line.append(f"[{label}]", style=color)
    line.append(f" {strip_markup(status.message)}{suffix}")
    return line


def _values_suffix(status: CheckStatus) -> str:
    detail = ", ".join(f"{key}={value}" for key, value in plain_values(status.values, single_line=True).items())
    return f" ({detail})" if detail else ""
