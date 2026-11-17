"""The plugin interface that every Testudo check plugin implements.

This is the one piece of API external users build against, so it is kept
small and stable: message-type declaration, severity levels and graphic
zones, a message callback, a periodic tick, and a single status getter. Built-in plugins use
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


#: `CheckStatus.values` key suffix marking a value's narrow-terminal
#: alternative -- e.g. a plugin emits both "gauge" (a wide multi-segment
#: meter) and "gauge_compact" (a single colored square) for the same
#: metric, and the TUI's Detail Panel picks whichever fits its current
#: width. A plain naming convention, not a CheckStatus schema change --
#: any plugin can opt in. Non-TUI outputs (published DiagnosticStatus,
#: `replay` text) drop the compact variant.
COMPACT_VALUE_SUFFIX = "_compact"

#: `CheckStatus.values` key suffix marking a secondary value, shown only
#: when the Detail Panel is tall enough to have room to spare (e.g. a
#: sparkline or a history strip next to the headline numbers it expands
#: on) and dropped first on a short one. Shown under its key without the
#: suffix. Same kind of naming convention as `COMPACT_VALUE_SUFFIX` (the
#: two combine as `<key>_more_compact`). The published DiagnosticStatus
#: keeps it; the one-line `replay` report drops it.
MORE_VALUE_SUFFIX = "_more"


@dataclass(frozen=True)
class CheckStatus:
    """A plugin's current rolled-up status.

    This single struct feeds both the TUI and the published
    diagnostic_msgs/DiagnosticStatus, so plugins only need to produce it once.
    `topic_panel_column`/`topic_panel_value` are TUI-only presentation data
    (ignored when publishing `DiagnosticStatus`, which has no slot for them):
    an optional short, already-formatted (Rich markup allowed) extra column
    for the Topic Panel, e.g. `topic_panel_column="Cov"`,
    `topic_panel_value="[yellow]0.031[/yellow]"`. A category only ever shows
    one plugin's topics at once, so this never competes for column budget
    with another plugin -- leave both empty (the default) to keep today's
    plain Topic/Status/Hz layout for that category. `codes` is the
    TUI_DATA_DESIGN.md error-code proposal: every *currently active*
    problem this status represents, as {code: already-formatted message}
    (e.g. `{"ODOM-001": "[bold red]position covariance trace ...[/bold red]"}`)
    -- a topic can have several active at once, unlike `label`/`message`,
    which only ever describe one rolled-up state. Empty (the default) means
    no plugin-specific problem is currently active; a liveness problem
    (no publishers, stale, ...) gets its own LIVE-* codes independently, in
    `topic_report.py`, since liveness is checked outside any plugin.

    `code_severities` is each code's own severity ({code: Severity}),
    which can be lower than the status's: a GNSS topic in ERROR for no fix
    can also carry a WARN for a changed frame_id. "Needs attention" and the
    fault history rank and record each code by it. A code missing from it
    counts at the status's severity (`code_severity`), so a plugin that
    only fills `codes` still works. `ActiveCodes` builds both at once.

    `values` is for people (the Detail Panel: markup, meters, sparklines);
    `metrics` is for machines: what the published DiagnosticStatus carries,
    so PlotJuggler/Foxglove can plot it. Each entry is a raw number
    (`{"success_rate": 0.5}`, a ratio as 0..1, units in the key's suffix as
    with `values`) or, sparingly, a short plain identifier
    (`{"frame_id": "base_link"}`). When `metrics` is non-empty it replaces
    `values` in published output; left empty (the default), the published
    status falls back to `values` as plain text (see
    `core.plain_values.published_values`), so a plugin that never sets it
    still publishes something readable.
    """

    severity: int
    label: str
    message: str
    values: dict[str, str] = field(default_factory=dict)
    topic_panel_column: str = ""
    topic_panel_value: str = ""
    codes: dict[str, str] = field(default_factory=dict)
    metrics: dict[str, float | str] = field(default_factory=dict)
    code_severities: dict[str, int] = field(default_factory=dict)


def code_severity(status: CheckStatus, code: str) -> int:
    """`code`'s own severity in `status`, or the status's when the plugin didn't give one."""
    return status.code_severities.get(code, status.severity)


@dataclass
class ActiveCodes:
    """Builds a `CheckStatus`'s `codes` and `code_severities` together.

    `active.add("ODOM-001", "position covariance high", Severity.WARN)`
    colors the message by its severity and records both; then pass
    `codes=active.messages, code_severities=active.severities`.
    """

    messages: dict[str, str] = field(default_factory=dict)
    severities: dict[str, int] = field(default_factory=dict)

    def add(self, code: str, message: str, severity: int) -> None:
        """Record `code` as active, with `message` (colored by `severity`) and `severity`."""
        self.messages[code] = colorize(message, severity)
        self.severities[code] = severity


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
        *,
        levels: dict[str, SeverityLevels] | None = None,
    ) -> None:
        """Construct a plugin instance.

        Args:
            thresholds: this instance's graphic zones (meter colors) --
                the caller (SubscriptionManager) has already merged any
                per-topic config overrides over `default_thresholds()`.
                Defaults to `default_thresholds()` if not given.
            related_topics: additional topics this instance also wants
                delivered via `on_message`, as {logical_name: topic_name},
                resolved from the declaring topic's config. Empty if this
                plugin doesn't use any (most don't).
            levels: this instance's severity levels, config overrides
                already merged over `default_levels()` (which it defaults to).
                A plugin overriding `__init__` must accept and pass it on.
        """
        self.thresholds = thresholds if thresholds is not None else self.default_thresholds()
        self.related_topics = related_topics or {}
        self.levels = levels if levels is not None else self.default_levels()

    @classmethod
    @abc.abstractmethod
    def msg_types(cls) -> tuple[str, ...]:
        """Fully-qualified message types this plugin handles, e.g. ('nav_msgs/msg/Odometry',)."""

    @classmethod
    def default_thresholds(cls) -> dict[str, ThresholdZone]:
        """Default graphic zones (meter colors), keyed by metric name. Empty unless overridden."""
        return {}

    @classmethod
    def default_levels(cls) -> dict[str, SeverityLevels]:
        """Default severity levels, keyed by metric name: what turns a topic WARN/ERROR.

        Users override them per topic (`thresholds: {metric: {warn: .., error: ..}}`).
        Empty unless overridden.
        """
        return {}

    @classmethod
    def presence_only_msg_types(cls) -> frozenset[str]:
        """Subset of `msg_types()` that default to Testudo's presence-only tier when undeclared.

        Presence-only means `SubscriptionManager` doesn't subscribe at all
        for an undeclared topic of this type -- just the discovery check
        every topic already gets (a publisher exists, or doesn't) -- because
        even a raw vitals subscription isn't actually cheap for this wire
        format. Image and point-cloud data are the motivating case: heavy
        enough, and numerous enough per sensor (raw plus several compressed/
        codec variants, or a dense 3D scan), that auto-watching every one of
        them measurably starves other nodes on a real robot.

        Declaring a topic of one of these types under `topics:` still opts
        it into this plugin's full checks regardless -- this only changes
        the *undeclared* default. Empty by default: most message types are
        cheap enough that vitals-tier auto-watching is the right default.
        """
        return frozenset()

    @classmethod
    def needs_every_message(cls) -> bool:
        """True when skipping a message would lose information, not just a sample.

        High-rate topics are decimated to `max_check_rate_hz` by default:
        on a sensor stream the next message says much the same as the one
        skipped. That's false on a multiplexed topic like `/tf`, where each
        message may be the only update of its own frame pair -- decimating
        it can starve one publisher's edges entirely (in a simulator, whose
        timers all fire on the same clock tick, the same publisher every
        time). Such a plugin returns True: `on_tick` + `on_message` then
        run for every message, while `get_status` is still only evaluated
        at the decimated rate, so `on_message` should stay cheap.
        """
        return False

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
        """Clear accumulated rolling-window state, keeping thresholds/related_topics/levels.

        Backs the TUI's 'reset stats' keybind. The default just re-runs
        `__init__` with the current thresholds/related_topics/levels --
        correct for any plugin (every built-in) whose state lives entirely
        in instance attributes set there. Override only if a plugin needs
        different reset semantics.
        """
        self.__init__(self.thresholds, self.related_topics, levels=self.levels)
