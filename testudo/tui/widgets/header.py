"""Status Bar: hostname, ROS distro, uptime, clock source, pause state."""
from __future__ import annotations

import socket
import time

from textual.reactive import reactive
from textual.widgets import Static


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

    def __init__(self, ros_distro: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._ros_distro = ros_distro or "unknown"
        self._hostname = socket.gethostname()
        self._started_monotonic = time.monotonic()

    def on_mount(self) -> None:
        self._refresh_uptime()
        self.set_interval(self.UPTIME_REFRESH_INTERVAL_SECONDS, self._refresh_uptime)

    def _refresh_uptime(self) -> None:
        self.uptime_seconds = time.monotonic() - self._started_monotonic

    def _render_text(self) -> str:
        # Wall clock is the overwhelmingly common case, so it's left implied
        # rather than spelled out; "clock: sim" only appears when sim time
        # is actually active, since that's the case worth flagging.
        clock_segment = "  |  clock: sim" if self.sim_time_active else ""
        flags = " [b][PAUSED][/b]" if self.paused else ""
        return (
            f"[b]testudo[/b]  |  {self._hostname}  |  ROS {self._ros_distro}  |  "
            f"uptime {format_duration(self.uptime_seconds)}{clock_segment}{flags}"
        )

    def watch_uptime_seconds(self) -> None:
        self.update(self._render_text())

    def watch_sim_time_active(self) -> None:
        self.update(self._render_text())

    def watch_paused(self) -> None:
        self.update(self._render_text())
