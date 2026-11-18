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

from testudo.plugins.severity import (  # noqa: F401 -- part of this module's public interface
    SEVERITY_COLORS,
    SEVERITY_ICONS,
    SEVERITY_LABELS,
    Severity,
    SeverityLevels,
    ThresholdZone,
    colorize,
    evaluate_levels,
    evaluate_zone,
)


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
    (e.g. `{"LOC-001": "[bold red]position covariance trace ...[/bold red]"}`)
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

    `active.add("LOC-001", "position covariance high", Severity.WARN)`
    colors the message by its severity and records both; then pass
    `codes=active.messages, code_severities=active.severities`.
    """

    messages: dict[str, str] = field(default_factory=dict)
    severities: dict[str, int] = field(default_factory=dict)

    def add(self, code: str, message: str, severity: int) -> None:
        """Record `code` as active, with `message` (colored by `severity`) and `severity`."""
        self.messages[code] = colorize(message, severity)
        self.severities[code] = severity


class CheckPlugin(abc.ABC):
    """Base contract for a Testudo check plugin.

    A plugin instance is bound to one primary topic (and optionally a small
    set of related topics it also wants to see -- e.g. a localization plugin
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
            levels: this instance's severity levels, defaulting to
                `default_levels()`. Testudo itself assigns `self.levels`
                (config overrides merged over the defaults) right after
                construction instead, so a plugin overriding `__init__`
                doesn't need to accept this keyword.
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

    @classmethod
    def default_related_topics(cls, topic_name: str) -> dict[str, str]:
        """Companion topics that follow from `topic_name` itself, as {logical name: topic}.

        For a companion fixed by convention, such as a costmap's
        `<topic>_updates`. Used only when the topic exists; an entry under
        the topic's `related_topics:` in config wins. Empty by default.
        """
        return {}

    def silence_expected(self, msg_type: str) -> bool:
        """True while this topic (of `msg_type`) is legitimately allowed to stop publishing.

        For a publisher whose silence depends on what's happening right now
        (a pose estimate only published while the robot moves), which a
        fixed `event_driven: true` can't express. Silence then reads as idle
        instead of stale, the status keeps being evaluated without new
        messages, and stamps are only checked for being in the future (an
        old stamp on a sparse topic says nothing). False by default.
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
        levels = self.levels
        self.__init__(self.thresholds, self.related_topics)
        self.levels = levels
