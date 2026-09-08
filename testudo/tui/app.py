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
from testudo.tui.theme import TESTUDO_THEME

_logger = logging.getLogger(__name__)

DEFAULT_POLL_RATE_HZ = 5.0


class TestudoApp(App):
    """The "neofetch for your nav stack" TUI."""

    CSS = """
    Screen { layout: vertical; }
    TestudoHeader { dock: top; height: 1; background: $primary; padding: 0 1; }
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
    /* Gold border-top, matching the nav-tips bar and the Topic Panel's
       selection below -- gold marks "the currently highlighted thing",
       and the detail pane is exactly that: the highlighted topic's data. */
    #detail-pane { height: 45%; border-top: solid $accent; padding: 0 1; overflow-y: auto; }
    /* height: auto (not a fixed 1) so the hint text can wrap onto a second
       line on a narrow terminal instead of being clipped -- a fixed height
       of 1 let Textual wrap the Text internally but then clipped everything
       past the first wrapped row, silently dropping the tail of the
       keybind list ("reset"/"help"/"quit"). A filled $accent background
       makes this read as its own footer band, the way a border-top line
       alone didn't -- a 1-row border drawn in a lighter color still sits
       on the same dark background as the pane above it, so it reads as a
       divider *within* one region rather than the edge of a visually
       distinct one. $text-muted (not a hardcoded color) is Textual's
       "auto"-contrast token -- it still picks a dark, readable foreground
       against gold, just dimmed to 60% opacity like it was against the
       previous background, keeping the hint text's de-emphasized look. */
    #hint { dock: bottom; height: auto; background: $accent; color: $text-muted; padding: 0 1; }
    #filter-input { dock: bottom; }
    #help-text { padding: 1 2; border: round $primary; }
    /* DataTable's own header row styling defaults to $panel/$foreground --
       overridden here (not per-table) so both the Plugin Panel's and Topic
       Panel's column headers pick up the neutral slate, distinguishing
       them from the Status Bar's now-red background above.
       #4b5563 (theme.py's TESTUDO_NEUTRAL), not a $-variable: it isn't
       part of the swappable primary/accent pair, and a custom variable
       name isn't resolvable at the initial stylesheet parse -- that
       happens before on_mount registers TESTUDO_THEME, against whichever
       theme Textual starts with by default, which has no such name. */
    DataTable > .datatable--header { background: #4b5563; color: $foreground; text-style: bold; }
    /* Row-cursor selection is gold in the Topic Panel only (not the Plugin
       Panel, which keeps DataTable's default primary/red cursor) -- gold
       marks "moving through one category's topics" specifically, rather
       than tinting every selection in the app the same way. The blurred
       (panel not focused) state uses a darkened, fully opaque gold rather
       than a translucent one -- alpha-blending over the dark background
       read as washed-out/desaturated rather than a deliberate "dimmer"
       gold. */
    #topic-table:focus > .datatable--cursor { background: $accent; color: #1a1a1a; }
    #topic-table > .datatable--cursor { background: $accent-darken-2; color: #1a1a1a; }
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
        self.register_theme(TESTUDO_THEME)
        self.theme = TESTUDO_THEME.name
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
