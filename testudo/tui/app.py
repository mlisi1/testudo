"""Testudo's Textual TUI: a live dashboard -- categories, that category's
topics, and the highlighted topic's full detail, all visible at once.

Driven by a live SubscriptionManager (`testudo watch`) or a finished
replay (`testudo replay --watch`) via the `WatchDataSource` the app is
constructed with -- the app itself doesn't know or care which.
"""
from __future__ import annotations

import logging
from typing import Callable

from textual.app import App

from testudo.tui.data_source import WatchDataSource, WatchSnapshot
from testudo.tui.keybinds import APP_BINDINGS
from testudo.tui.screens import DashboardScreen, HelpScreen

_logger = logging.getLogger(__name__)

DEFAULT_POLL_RATE_HZ = 5.0


class TestudoApp(App):
    """The "neofetch for your nav stack" TUI."""

    CSS = """
    Screen { layout: vertical; }
    TestudoHeader { dock: top; height: 1; background: $panel; padding: 0 1; }
    #panes { height: 1fr; }
    /* Both tables use fixed (not auto-fit) column widths, so their total
       width is bounded and known -- auto-sizing the panes to that, rather
       than splitting by percentage, means every column always fits without
       its own horizontal scroll, regardless of terminal width. Explicit
       height: 1fr so each pane's background fills the full vertical space
       down to the detail pane, not just however many rows it has data for.
       overflow-x: hidden is the enforced "no horizontal scrollbar" --
       content that doesn't fit a fixed column is clipped (Topic) or
       marqueed (Topic, when wider than its column), never scrolled to. */
    #category-table { width: auto; height: 1fr; overflow-x: hidden; }
    #topic-table { width: 1fr; min-width: 40; height: 1fr; overflow-x: hidden; }
    /* 45%, not 30% -- on a genuinely small pane (a quarter-tiled terminal,
       the realistic use case, not a fullscreen one) 30% of an already-short
       screen left the detail pane only 1-2 visible rows even after
       tightening its content, well short of a single topic's data. */
    #detail-pane { height: 45%; border-top: solid $primary; padding: 0 1; overflow-y: auto; }
    /* height: auto (not a fixed 1) so the hint text can wrap onto a second
       line on a narrow terminal instead of being clipped -- a fixed height
       of 1 let Textual wrap the Text internally but then clipped everything
       past the first wrapped row, silently dropping the tail of the
       keybind list ("reset"/"help"/"quit"). A filled $panel background
       (matching TestudoHeader's own treatment above) makes this read as its
       own footer band, the way a border-top line alone didn't -- a 1-row
       border drawn in a lighter color still sits on the same dark
       background as the pane above it, so it reads as a divider *within*
       one region rather than the edge of a visually distinct one. */
    #hint { dock: bottom; height: auto; background: $panel; color: $text-muted; padding: 0 1; }
    #filter-input { dock: bottom; }
    #help-text { padding: 1 2; border: round $primary; }
    """
    BINDINGS = APP_BINDINGS
    TITLE = "testudo"

    def __init__(
        self,
        data_source: WatchDataSource,
        ros_distro: str,
        ros_domain_id: str = "0",
        dds_implementation: str = "unknown",
        poll_rate_hz: float = DEFAULT_POLL_RATE_HZ,
        sim_time_active: bool = False,
        on_snapshot: Callable[[WatchSnapshot], None] | None = None,
    ) -> None:
        super().__init__()
        self.data_source = data_source
        self.ros_distro = ros_distro
        self.ros_domain_id = ros_domain_id
        self.dds_implementation = dds_implementation
        self.sim_time_active = sim_time_active
        self.paused = False
        self._poll_rate_hz = poll_rate_hz
        self._on_snapshot = on_snapshot
        self.latest_snapshot: WatchSnapshot = data_source.poll()

    def on_mount(self) -> None:
        self.push_screen(DashboardScreen())
        if self.data_source.is_live():
            self.set_interval(1.0 / self._poll_rate_hz, self._poll)

    def _poll(self, *, force: bool = False) -> None:
        if self.paused and not force:
            return
        self.latest_snapshot = self.data_source.poll()
        if self._on_snapshot is not None:
            self._on_snapshot(self.latest_snapshot)
        self._notify_dashboard()

    def _notify_dashboard(self) -> None:
        for screen in self.screen_stack:
            if isinstance(screen, DashboardScreen):
                screen.on_snapshot(self.latest_snapshot)

    def action_toggle_pause(self) -> None:
        if not self.data_source.is_live():
            return
        self.paused = not self.paused
        for screen in self.screen_stack:
            if isinstance(screen, DashboardScreen):
                screen.sync_header()

    def action_reset_stats(self) -> None:
        self.data_source.reset_stats()
        self._poll(force=True)

    def action_show_help(self) -> None:
        self.push_screen(HelpScreen())
