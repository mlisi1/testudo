"""Status Bar: ROS distro, domain ID, DDS implementation, uptime, clock source, pause
state -- and, right-aligned, the overall verdict across every checked topic."""
from __future__ import annotations

import time

from rich.cells import cell_len
from rich.text import Text
from textual.reactive import reactive
from textual.widgets import Static

from testudo.plugins.base import SEVERITY_COLORS, SEVERITY_ICONS, CheckStatus, Severity


_SEGMENT_SEPARATOR = " [dim]│[/dim] "

#: The verdict chip's word for each overall (worst-mode) severity.
_VERDICT_WORDS = {
    Severity.OK: "NOMINAL",
    Severity.WARN: "DEGRADED",
    Severity.ERROR: "FAULT",
    Severity.STALE: "STALE",
}

#: Worst-first, matching the Plugin Panel's breakdown; OK is left out of
#: the verdict's counts -- the chip already says whether everything is fine.
_COUNT_ORDER = (Severity.STALE, Severity.ERROR, Severity.WARN)

#: Below this many columns the DDS segment is dropped and the weighted
#: gauge shrinks to its percentage: the verdict matters more than the rmw
#: implementation's name, and has to fit beside the rest at 101 columns.
WIDE_STATUS_BAR_WIDTH = 120

_GAUGE_SEGMENTS = 10
_GAUGE_PARTIALS = " ▏▎▍▌▋▊▉"


def weighted_health(overall: CheckStatus) -> float | None:
    """The weighted score as a 0..1 health fraction (1 = all OK, 0 = all ERROR or worse), if reported."""
    raw = overall.values.get("weighted_score")
    if raw is None:
        return None
    try:
        score = float(raw)
    except ValueError:
        return None
    return max(0.0, min(1.0, 1.0 - score / Severity.ERROR))


def health_gauge(fraction: float, segments: int = _GAUGE_SEGMENTS) -> str:
    """A `segments`-wide eighth-block bar for `fraction` (0..1), between `▕`/`▏` end caps."""
    eighths = round(max(0.0, min(1.0, fraction)) * segments * 8)
    full, partial = divmod(eighths, 8)
    bar = "█" * full + (_GAUGE_PARTIALS[partial] if partial else "")
    return f"▕{bar.ljust(segments)}▏"


def verdict_markup(overall: CheckStatus, counts: dict[int, int], wide: bool) -> str:
    """The right-aligned verdict: severity chip, non-OK counts, and the weighted gauge when reported."""
    color = SEVERITY_COLORS.get(overall.severity, "white")
    word = _VERDICT_WORDS.get(overall.severity, str(overall.severity))
    parts = [f"[b {color}]{SEVERITY_ICONS.get(overall.severity, '?')} {word}[/b {color}]"]
    for severity in _COUNT_ORDER:
        if counts.get(severity, 0):
            severity_color = SEVERITY_COLORS[severity]
            parts.append(f"[{severity_color}]{SEVERITY_ICONS[severity]}{counts[severity]}[/{severity_color}]")
    health = weighted_health(overall)
    if health is not None:
        gauge = f"[dim]weighted[/dim] {health_gauge(health)} " if wide else ""
        parts.append(f" {gauge}{health:.0%}")
    return " ".join(parts)


def format_duration(seconds: float) -> str:
    """A compact `1h02m03s` / `2m03s` / `3s` duration string."""
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{secs:02d}s"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


class TestudoHeader(Static):
    """A one-line status bar refreshed by the app's poll timer.

    Reactive attributes so re-render only happens when a value actually
    changes -- Textual skips `watch_*` for an unchanged value by default.
    """

    UPTIME_REFRESH_INTERVAL_SECONDS = 1.0

    uptime_seconds: reactive[float] = reactive(0.0)
    sim_time_active: reactive[bool] = reactive(False)
    paused: reactive[bool] = reactive(False)
    #: (overall status, per-severity topic counts), from the latest poll.
    verdict: reactive[tuple[CheckStatus, dict[int, int]] | None] = reactive(None)

    def __init__(
        self,
        ros_distro: str,
        ros_domain_id: str = "0",
        dds_implementation: str = "unknown",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._ros_distro = ros_distro or "unknown"
        self._ros_domain_id = ros_domain_id or "0"
        self._dds_implementation = dds_implementation or "unknown"
        self._started_monotonic = time.monotonic()

    def on_mount(self) -> None:
        self._refresh_uptime()
        self.set_interval(self.UPTIME_REFRESH_INTERVAL_SECONDS, self._refresh_uptime)

    def _refresh_uptime(self) -> None:
        self.uptime_seconds = time.monotonic() - self._started_monotonic

    def _render_text(self) -> str:
        wide = self.size.width >= WIDE_STATUS_BAR_WIDTH
        left = self._left_text(wide)
        if self.verdict is None:
            return left
        right = verdict_markup(*self.verdict, wide=wide)
        # Padding ends the right-hand side flush with the bar's content edge;
        # at least two spaces apart even when the bar is too narrow for both.
        content_width = self.content_size.width or self.size.width
        gap = content_width - cell_len(Text.from_markup(left).plain) - cell_len(Text.from_markup(right).plain)
        return f"{left}{' ' * max(2, gap)}{right}"

    def _left_text(self, wide: bool) -> str:
        # Wall clock is the overwhelmingly common case, so it's left implied
        # rather than spelled out; "clock: sim" only appears when sim time
        # is actually active, since that's the case worth flagging.
        # Labels dimmed, values at full strength: the eye lands on what
        # varies between robots/runs, not on the words "ROS"/"domain".
        segments = [
            f"[dim]ROS[/dim] {self._ros_distro}",
            f"[dim]domain[/dim] {self._ros_domain_id}",
            *([self._dds_implementation] if wide else []),
            f"[dim]up[/dim] {format_duration(self.uptime_seconds)}",
        ]
        if self.sim_time_active:
            segments.append("[dim]clock[/dim] sim")
        flags = "  [b reverse] PAUSED [/b reverse]" if self.paused else ""
        return f"[b]testudo[/b]  {_SEGMENT_SEPARATOR.join(segments)}{flags}"

    def watch_uptime_seconds(self) -> None:
        self.update(self._render_text())

    def watch_sim_time_active(self) -> None:
        self.update(self._render_text())

    def watch_paused(self) -> None:
        self.update(self._render_text())

    def watch_verdict(self) -> None:
        self.update(self._render_text())

    def on_resize(self) -> None:
        self.update(self._render_text())
