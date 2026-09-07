"""The plugin interface that every Testudo check plugin implements.

This is the one piece of API external users build against, so it is kept
small and stable: message-type declaration, a threshold profile, a message
callback, a periodic tick, and a single status getter. Built-in plugins use
this exact interface too, rather than a privileged internal one.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any

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
#: understand the identical syntax, so `testudo check`'s plain-text report
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
    """Boundary values for one metric's green/orange/red zones.

    Direction (higher-is-better vs. lower-is-better) is a matter of
    convention between a plugin and its threshold consumer; this struct only
    carries the three boundary values.
    """

    green: float | None = None
    orange: float | None = None
    red: float | None = None


@dataclass(frozen=True)
class CheckStatus:
    """A plugin's current rolled-up status.

    This single struct feeds both the TUI and the published
    diagnostic_msgs/DiagnosticStatus, so plugins only need to produce it once.
    """

    severity: int
    label: str
    message: str
    values: dict[str, str] = field(default_factory=dict)


def evaluate_zone(value: float, zone: ThresholdZone | None, *, higher_is_worse: bool = True) -> int:
    """Map `value` to a Severity using `zone`'s green/orange cut points.

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


class CheckPlugin(abc.ABC):
    """Base contract for a Testudo check plugin.

    A plugin instance is bound to one primary topic (and optionally a small
    set of related topics it also wants to see -- e.g. an odometry plugin
    reading `/cmd_vel` for a cross-check). It receives (decimated, on
    high-rate topics) typed messages via `on_message`, an independent
    periodic tick via `on_tick` for time-based stats that must advance even
    without new messages (e.g. staleness), and reports its current state on
    demand via `get_status`.
    """

    def __init__(
        self,
        thresholds: dict[str, ThresholdZone] | None = None,
        related_topics: dict[str, str] | None = None,
    ) -> None:
        """Construct a plugin instance.

        Args:
            thresholds: this instance's effective threshold profile --
                the caller (SubscriptionManager) has already merged any
                per-topic config overrides over `default_thresholds()`.
                Defaults to `default_thresholds()` if not given.
            related_topics: additional topics this instance also wants
                delivered via `on_message`, as {logical_name: topic_name},
                resolved from the declaring topic's config. Empty if this
                plugin doesn't use any (most don't).
        """
        self.thresholds = thresholds if thresholds is not None else self.default_thresholds()
        self.related_topics = related_topics or {}

    @classmethod
    @abc.abstractmethod
    def msg_types(cls) -> tuple[str, ...]:
        """Fully-qualified message types this plugin handles, e.g. ('nav_msgs/msg/Odometry',)."""

    @classmethod
    def default_thresholds(cls) -> dict[str, ThresholdZone]:
        """Default threshold profile, keyed by metric name. Empty unless overridden."""
        return {}

    @abc.abstractmethod
    def on_message(self, topic: str, msg: Any) -> None:
        """Handle one (possibly decimated) message received on `topic`.

        `topic` is one of this instance's primary topic or one of the real
        topic names in `related_topics.values()` -- check which by name if
        the plugin subscribes to more than one.
        """

    def on_tick(self, now_seconds: float) -> None:
        """Advance time-based state on the aggregator's fixed tick.

        Called independent of message arrival. Default is a no-op; override
        only if the plugin tracks something that must update without new
        messages, such as staleness.
        """

    @abc.abstractmethod
    def get_status(self) -> CheckStatus:
        """Return the plugin's current rolled-up status."""

    def reset(self) -> None:
        """Clear accumulated rolling-window state, keeping thresholds/related_topics.

        Backs the TUI's 'reset stats' keybind. The default just re-runs
        `__init__` with the current thresholds/related_topics -- correct
        for any plugin (every built-in) whose state lives entirely in
        instance attributes set there. Override only if a plugin needs
        different reset semantics.
        """
        self.__init__(self.thresholds, self.related_topics)
