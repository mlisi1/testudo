"""Testudo's Textual TUI: a live dashboard -- categories, that category's
topics, and the highlighted topic's full detail, all visible at once.

Driven by a live SubscriptionManager (`testudo watch`) or a finished
replay (`testudo replay --watch`) via the `WatchDataSource` the app is
constructed with -- the app itself doesn't know or care which.
"""
from __future__ import annotations

import logging
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from textual.app import App
from textual.css.query import NoMatches

from testudo.core.config import ConfigError
from testudo.tui.data_source import WatchDataSource, WatchSnapshot
from testudo.tui.display_prefs import DisplayPrefs, save_display_pref
from testudo.tui.replay_source import ReplayTimeline
from testudo.tui.keybinds import APP_BINDINGS
from testudo.tui.options_screen import OptionsMenuScreen
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
    /* The Plugin Panel's table takes only its rows' height, so the
       "Needs attention" list (widgets/attention.py) can use what's left
       under it; a long category list takes the whole pane and the list
       gets no rows, which hides it. The pane's background is the table's
       ($surface), so the two read as one panel. */
    #left-pane { width: auto; height: 1fr; background: $surface; }
    #category-table { width: auto; height: auto; max-height: 100%; overflow-x: hidden; }
    #attention { width: 100%; height: 1fr; padding: 1 1 0 1; }
    #topic-table { width: 1fr; min-width: 40; height: 1fr; overflow-x: hidden; }
    /* 45% only until the first layout: from then on DashboardScreen.
       refresh_detail sets the height from the highlighted topic's content
       (tui/pane_split.py), the panes above taking the rest. */
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
    #quick-actions { dock: bottom; height: auto; background: $primary; padding: 0 1; }
    /* Every modal screen (help, options menu, exclude topics) centers its
       one panel in the middle of the screen it's dimming -- ModalScreen's
       own default CSS only sets layout/overflow/background, not
       alignment, so left alone a panel renders pinned to the top-left
       corner of an otherwise-full-height, mostly-empty overlay. */
    HelpScreen, OptionsMenuScreen, ExcludeTopicsScreen, SettingsScreen { align: center middle; }
    #help-text { padding: 0 2; border: round $primary; }
    #options-menu-panel { padding: 1 2; border: round $primary; width: 70%; height: auto; }
    #options-menu-body, #settings-body { height: auto; }
    #options-menu-list { width: 22; height: auto; }
    #options-menu-help, #settings-help { width: 1fr; height: auto; padding: 1 0 1 2; color: $text-muted; }
    #options-menu-hint { height: 1; color: $text-muted; }
    #options-panel { padding: 1 2; border: round $primary; width: 80%; height: auto; max-height: 80%; }
    #exclude-table { height: 10; }
    #settings-panel { padding: 1 2; border: round $primary; width: 90%; height: auto; }
    #settings-list { width: 36; height: auto; max-height: 12; }
    #options-preview { height: 1; color: $text-muted; }
    #options-message { height: auto; color: $text-muted; }
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
        display_prefs: DisplayPrefs | None = None,
        display_prefs_path: Path | None = None,
    ) -> None:
        super().__init__()
        self.display_prefs = display_prefs or DisplayPrefs()
        self._display_prefs_path = display_prefs_path
        self._last_draw_monotonic = 0.0
        self.data_source = data_source
        self.ros_distro = ros_distro
        self.ros_domain_id = ros_domain_id
        self.dds_implementation = dds_implementation
        self.sim_time_active = sim_time_active
        self.paused = False
        self._poll_rate_hz = poll_rate_hz
        self._on_snapshot = on_snapshot
        self.latest_snapshot: WatchSnapshot = data_source.poll()
        self._last_playback_monotonic = time.monotonic()

    def on_mount(self) -> None:
        self.register_theme(TESTUDO_THEME)
        self.theme = TESTUDO_THEME.name
        self.push_screen(DashboardScreen())
        if self.data_source.is_live():
            self.set_interval(1.0 / self._poll_rate_hz, self._poll)
        elif self.timeline is not None:
            self.set_interval(1.0 / self._poll_rate_hz, self._advance_playback)

    @property
    def timeline(self) -> "ReplayTimeline | None":
        """The data source's replay timeline; None live (or for a source that predates it)."""
        return getattr(self.data_source, "timeline", None)

    def _advance_playback(self) -> None:
        """Move a playing replay on by the wall time since the last call (1x bag time)."""
        now = time.monotonic()
        elapsed = now - self._last_playback_monotonic
        self._last_playback_monotonic = now
        if self.timeline.advance(elapsed):
            self._poll(force=True)

    def refresh_now(self) -> None:
        """Show the effect of a change straight away, even while paused."""
        self._poll(force=True)

    def _poll(self, *, force: bool = False) -> None:
        if self.paused and not force:
            return
        self.latest_snapshot = self.data_source.poll()
        if self._on_snapshot is not None:
            self._on_snapshot(self.latest_snapshot)
        if force or self._draw_is_due():
            self._notify_dashboard()

    def _draw_is_due(self) -> bool:
        """Whether the display's refresh rate allows a redraw; polling and publishing go on regardless."""
        now = time.monotonic()
        # 0.8: a timer firing a little early mustn't skip every other frame.
        if now - self._last_draw_monotonic < 0.8 / self.display_prefs.refresh_hz:
            return False
        self._last_draw_monotonic = now
        return True

    @property
    def poll_rate_hz(self) -> float:
        """How often new data arrives, which limits the display's refresh rate."""
        return self._poll_rate_hz

    def change_display_pref(self, key: str, value: Any) -> str:
        """Apply one Options > Display setting and save it to the per-user file; says where it went."""
        self.display_prefs = replace(self.display_prefs, **{key: value})
        for screen in self.screen_stack:
            if isinstance(screen, DashboardScreen):
                screen.apply_display_prefs()
        if self._display_prefs_path is None:
            return "for this session only: no preferences file"
        try:
            save_display_pref(self._display_prefs_path, key, value)
        except (OSError, ConfigError) as exc:
            _logger.warning("could not save to %s: %s", self._display_prefs_path, exc)
            return f"for this session only: could not save ({exc})"
        return f"saved to {self._display_prefs_path}"

    def _notify_dashboard(self) -> None:
        for screen in self.screen_stack:
            if isinstance(screen, DashboardScreen):
                try:
                    screen.on_snapshot(self.latest_snapshot)
                except NoMatches:
                    # The poll timer can still fire while the app is
                    # exiting, after the dashboard's widgets were already
                    # removed: nothing is left to draw into.
                    return

    def action_toggle_pause(self) -> None:
        timeline = self.timeline
        if timeline is not None:
            # A replay has nothing live to pause: `p` plays/pauses the bag instead.
            timeline.toggle_play()
            self._last_playback_monotonic = time.monotonic()
            self._poll(force=True)
            return
        if not self.data_source.is_live():
            return
        self.paused = not self.paused
        for screen in self.screen_stack:
            if isinstance(screen, DashboardScreen):
                screen.sync_header()

    def action_step_frames(self, frames: int) -> None:
        """`[`/`]` (`{`/`}`): step a replay's frames back/forward, pausing playback. No-op live."""
        timeline = self.timeline
        if timeline is None:
            return
        timeline.step(frames)
        # Polled even when already at an end: playback just stopped, and the Status Bar shows it.
        self._poll(force=True)

    def action_reset_stats(self) -> None:
        self.data_source.reset_stats()
        self._poll(force=True)

    def action_show_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_show_options(self) -> None:
        self.push_screen(OptionsMenuScreen(self.data_source))
