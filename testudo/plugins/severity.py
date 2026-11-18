"""Severities, the levels that decide them, and the zones that color meters."""
from __future__ import annotations

from dataclasses import dataclass

from diagnostic_msgs.msg import DiagnosticStatus


class Severity:
    """Severity levels, numerically aligned with diagnostic_msgs/DiagnosticStatus.

    DiagnosticStatus's level field is IDL type `byte`, which rosidl/rclpy
    represents as single-byte `bytes` objects, not `int` -- that breaks
    ordering/arithmetic (e.g. `max()` across severities, sort keys). Convert
    once here so the rest of Testudo can treat severity as a plain int.
    """

    OK = int.from_bytes(DiagnosticStatus.OK, byteorder="little")
    WARN = int.from_bytes(DiagnosticStatus.WARN, byteorder="little")
    ERROR = int.from_bytes(DiagnosticStatus.ERROR, byteorder="little")
    STALE = int.from_bytes(DiagnosticStatus.STALE, byteorder="little")


#: Display label per severity, shared by the CLI report and the aggregator's
#: summary message so both describe severities the same way.
SEVERITY_LABELS: dict[int, str] = {
    Severity.OK: "OK",
    Severity.WARN: "WARN",
    Severity.ERROR: "ERROR",
    Severity.STALE: "STALE",
}

#: Display color per severity, as a Rich/Textual style name -- both the
#: TUI's markup (`[green]...[/green]`) and `rich.console.Console.print`
#: understand the identical syntax, so `testudo replay`'s plain-text report
#: and the TUI stay visually consistent from one shared mapping.
SEVERITY_COLORS: dict[int, str] = {
    Severity.OK: "green",
    Severity.WARN: "yellow",
    Severity.ERROR: "bold red",
    Severity.STALE: "bold magenta",
}

#: Single-cell-width glyph per severity, for compact breakdowns (e.g. the
#: TUI's category summary: "[red]✗ 1[/red] [yellow]▲ 2[/yellow]") where a
#: full "ERROR"/"WARN" label per entry would take more room than the count
#: itself. A distinct shape per severity (not just color) keeps it readable
#: without color too.
SEVERITY_ICONS: dict[int, str] = {
    Severity.OK: "✓",  # check mark
    Severity.WARN: "▲",  # up-pointing triangle
    Severity.ERROR: "✗",  # ballot X
    Severity.STALE: "■",  # black square
}


@dataclass(frozen=True)
class ThresholdZone:
    """Boundary values for one metric's green/orange/red graphic zones.

    Graphics only: how a meter or trend is colored and scaled
    (`plugins.render.meter`). Whether a topic is WARN or ERROR is decided by
    `SeverityLevels`, separately. Direction (higher-is-better vs.
    lower-is-better) is a matter of convention between a plugin and its
    threshold consumer; this struct only carries the three boundary values.
    """

    green: float | None = None
    orange: float | None = None
    red: float | None = None


@dataclass(frozen=True)
class SeverityLevels:
    """Where one metric turns a topic WARN and ERROR (`evaluate_levels`).

    ERROR means something has gone wrong (a 9999 placeholder covariance, a
    frozen driver); a working but degraded reading is a WARN. A missing
    cut never fires: no `error` means the metric can degrade a topic but
    never break it. Separate from `ThresholdZone`, which only colors meters.
    """

    warn: float | None = None
    error: float | None = None


def colorize(text: str, severity: int) -> str:
    """Wrap `text` in Rich markup for `severity`'s `SEVERITY_COLORS` entry."""
    color = SEVERITY_COLORS.get(severity, "white")
    return f"[{color}]{text}[/{color}]"


def evaluate_levels(value: float, levels: SeverityLevels | None, *, higher_is_worse: bool = True) -> int:
    """Map `value` to a Severity: past `levels.error` ERROR, past `levels.warn` WARN, else OK.

    "Past" is above for `higher_is_worse=True` (the default: covariance,
    error counts) and below for `higher_is_worse=False` (a success rate, an
    update rate). A value exactly on a cut is still on the good side.
    `levels=None` (nothing configured for this metric) is always OK.
    """
    if levels is None:
        return Severity.OK

    def past(cut: float | None) -> bool:
        if cut is None:
            return False
        return value > cut if higher_is_worse else value < cut

    if past(levels.error):
        return Severity.ERROR
    if past(levels.warn):
        return Severity.WARN
    return Severity.OK


def evaluate_zone(value: float, zone: ThresholdZone | None, *, higher_is_worse: bool = True) -> int:
    """Map `value` to a Severity using `zone`'s green/orange cut points.

    Kept for plugins written before `SeverityLevels` (built-ins use
    `evaluate_levels`, so their zones only color meters); new code should
    judge severity with `evaluate_levels` instead.

    `higher_is_worse=True` (the default) treats `value <= green` as OK,
    `value <= orange` as WARN, and anything past that as ERROR -- for
    metrics where growing is bad (e.g. covariance magnitude, error counts).
    Pass `higher_is_worse=False` for metrics where *shrinking* is bad (e.g.
    a signal strength or update rate), which flips both comparisons.

    A missing boundary means "unconstrained at that level": no `green` means
    nothing is ever OK; no `orange` either means anything real is ERROR.
    `zone.red` isn't used here -- it's accepted by the config schema purely
    as display metadata (e.g. a future TUI gauge's outer bound), not as a
    third severity cut point.

    `zone=None` (no threshold configured for this metric, plugin default or
    user override) always returns OK -- an unconfigured metric can't fail.
    """
    if zone is None:
        return Severity.OK
    if higher_is_worse:
        if zone.green is not None and value <= zone.green:
            return Severity.OK
        if zone.orange is not None and value <= zone.orange:
            return Severity.WARN
        return Severity.ERROR
    else:
        if zone.green is not None and value >= zone.green:
            return Severity.OK
        if zone.orange is not None and value >= zone.orange:
            return Severity.WARN
        return Severity.ERROR
