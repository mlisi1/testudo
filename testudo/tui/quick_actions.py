"""Quick actions on the highlighted topic (`a`), offered in place of the hint bar.

One line, no extra screen: mute or downgrade one of the topic's codes, set
the `warn` level of the metric behind it from its current value, or exclude
the topic. Each takes effect at once and is saved to the loaded config.
What the line offers is fixed when it opens, so the value shown is the value saved.
"""
from __future__ import annotations

from dataclasses import dataclass

from textual.binding import Binding
from textual.widget import Widget
from textual.widgets import Static

from testudo.core.config import ExcludeRule
from testudo.core.topic_report import TopicReport
from testudo.plugins.base import SEVERITY_LABELS, CodeMetric, Severity, code_severity
from testudo.tui.data_source import LiveDataSource

#: Rows with nothing behind them to change: a node's process, an excluded topic.
_NO_ACTION_TIERS = frozenset({"node", "excluded"})


def can_act_on(report: TopicReport) -> bool:
    """Whether `report` is a topic the quick actions apply to."""
    return report.tier not in _NO_ACTION_TIERS


@dataclass(frozen=True)
class CodeChoice:
    """One code the line can act on: active now, or overridden and possibly hidden by that."""

    code: str
    severity: int | None  # None: not active right now
    overridden: bool
    metric: CodeMetric | None = None
    warn_cut: float | None = None


def code_choices(report: TopicReport, source: LiveDataSource) -> list[CodeChoice]:
    """The topic's active codes, worst first, then the overridden codes a mute has hidden."""
    status = report.status
    overrides = source.code_overrides(report)
    choices = []
    for code in sorted(status.codes, key=lambda code: (-code_severity(status, code), code)):
        metric = status.code_metrics.get(code)
        cut = source.warn_cut(report, metric) if metric is not None else None
        choices.append(CodeChoice(code, code_severity(status, code), code in overrides, metric, cut))
    choices.extend(CodeChoice(code, None, True) for code in sorted(set(overrides) - set(status.codes)))
    return choices


def line_markup(report: TopicReport, choices: list[CodeChoice], index: int) -> str:
    """The line for `choices[index]`, offering only what applies to it."""
    parts = []
    if choices:
        choice = choices[index]
        state = SEVERITY_LABELS.get(choice.severity, "") if choice.severity is not None else "overridden, not active"
        parts.append(f"[b]{choice.code}[/b] {state}")
        if choice.severity is not None and choice.severity > Severity.WARN:
            parts.append("[b]w[/b] downgrade to warn")
        if choice.severity is not None:
            parts.append("[b]m[/b] mute")
        if choice.warn_cut is not None:
            parts.append(f"[b]l[/b] warn at {choice.warn_cut:g} ({choice.metric.name})")
        elif choice.metric is not None:
            parts.append(f"[dim]{choice.metric.name} is past its error level[/dim]")
        if choice.overridden:
            parts.append("[b]r[/b] restore")
        if len(choices) > 1:
            parts.append(f"[b]c[/b] next code ({index + 1}/{len(choices)})")
    if report.tier != "derived":
        parts.append("[b]x[/b] exclude topic")
    parts.append("[b]esc[/b] cancel")
    return " · ".join(parts)


class QuickActionBar(Static, can_focus=True):
    """The line itself: holds focus while open, and closes as soon as it loses it."""

    BINDINGS = [
        Binding("w", "downgrade", show=False),
        Binding("m", "mute", show=False),
        Binding("l", "warn_level", show=False),
        Binding("r", "restore", show=False),
        Binding("c", "next_code", show=False),
        Binding("x", "exclude", show=False),
        Binding("escape", "cancel", show=False),
    ]

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__("", id=id, markup=True)
        self.display = False
        self._report: TopicReport | None = None
        self._source: LiveDataSource | None = None
        self._choices: list[CodeChoice] = []
        self._index = 0
        self._return_focus: Widget | None = None

    def open(self, report: TopicReport, source: LiveDataSource, return_focus: Widget | None) -> None:
        """Offer `report`'s actions until one is chosen or focus moves on."""
        self._report, self._source, self._return_focus = report, source, return_focus
        self._choices = code_choices(report, source)
        self._index = 0
        self.update(line_markup(report, self._choices, self._index))
        self.screen.query_one("#hint").display = False
        self.display = True
        self.screen.set_focus(self)

    def on_blur(self) -> None:
        self.display = False
        self.screen.query_one("#hint").display = True

    def _close(self) -> None:
        self.screen.set_focus(self._return_focus)

    @property
    def _choice(self) -> CodeChoice | None:
        return self._choices[self._index] if self._choices else None

    def _done(self, what: str, outcome: str) -> None:
        self.notify(f"{what}, {outcome}", severity="warning" if "session only" in outcome else "information", timeout=6)
        self.app.refresh_now()
        self._close()

    def _override(self, severity: int | None, what: str) -> None:
        choice = self._choice
        self._done(f"{choice.code} {what} on {self._report.topic}", self._source.override_code(self._report, choice.code, severity))

    def action_downgrade(self) -> None:
        choice = self._choice
        if choice is not None and choice.severity is not None and choice.severity > Severity.WARN:
            self._override(Severity.WARN, "downgraded to warn")

    def action_mute(self) -> None:
        choice = self._choice
        if choice is not None and choice.severity is not None:
            self._override(Severity.OK, "muted")

    def action_restore(self) -> None:
        choice = self._choice
        if choice is not None and choice.overridden:
            self._override(None, "restored")

    def action_warn_level(self) -> None:
        choice = self._choice
        if choice is None or choice.warn_cut is None:
            return
        name = choice.metric.name
        outcome = self._source.set_warn_level(self._report, name, choice.warn_cut)
        self._done(f"{name} now warns at {choice.warn_cut:g} on {self._report.topic}", outcome)

    def action_next_code(self) -> None:
        if len(self._choices) > 1:
            self._index = (self._index + 1) % len(self._choices)
            self.update(line_markup(self._report, self._choices, self._index))

    def action_exclude(self) -> None:
        if self._report.tier == "derived":
            return
        self._source.add_exclude_rule(ExcludeRule(self._report.topic))
        self.notify(f"excluded {self._report.topic}", timeout=6)
        self.app.refresh_now()
        self._close()

    def action_cancel(self) -> None:
        self._close()
