"""Shared keybindings and the vim-nav DataTable subclass every panel uses.

Textual's DataTable already binds the arrow keys to cursor movement; this
module only adds the vim-style aliases and documents the rest of the
project's keybind set (`q` quit, `p` pause, `/` filter, `j`/`k`/arrows/Tab/
Left/Right navigate, `Enter` switch pane, `Esc` back to categories (or
clear the filter), `s` cycle sort, `r` reset stats, `o` options menu, `?`
help overlay) in one place other modules can point at.
"""
from __future__ import annotations

from textual.binding import Binding
from textual.widgets import DataTable

#: Global bindings, active regardless of which screen is on top.
APP_BINDINGS = [
    Binding("q", "quit", "Quit"),
    Binding("p", "toggle_pause", "Pause"),
    Binding("r", "reset_stats", "Reset stats"),
    Binding("o", "show_options", "Options"),
    Binding("question_mark", "show_help", "Help", key_display="?"),
]

#: Screen-level bindings (only meaningful where there's a table to filter/sort).
FILTER_BINDING = Binding("slash", "start_filter", "Filter", key_display="/")
SORT_BINDING = Binding("s", "cycle_sort", "Sort")

#: Left/Right switch between the Plugin Panel and Topic Panel, alongside
#: Tab/Shift+Tab (Textual's own default focus-cycling) and Enter (this
#: screen's own row-select handler) -- three ways to do the same thing,
#: matching how naturally each fits a different navigation style.
#: `priority=True`: DataTable itself already binds Left/Right (to a
#: cursor/scroll action, in `cursor_type="row"` mode a harmless no-op
#: since these tables never scroll horizontally) -- a non-priority
#: screen-level binding would never even be reached, since the focused
#: DataTable's own binding wins first. `DashboardScreen.check_action`
#: declines these two actions while the filter `Input` is focused, so a
#: priority match still falls through to the Input's own Left/Right (move
#: the text cursor) instead of hijacking it mid-search.
PANE_LEFT_BINDING = Binding("left", "focus_previous_pane", "Categories", priority=True, show=False)
PANE_RIGHT_BINDING = Binding("right", "focus_next_pane", "Topics", priority=True, show=False)

HELP_TEXT = """\
[b]Testudo -- keybindings[/b]

  q                quit
  p                pause / resume the live view
  r                reset stats (clear accumulated history, keep watching)
  o                options menu (exclude topics -- literal or regex)
  /                filter the focused table by name
  j / k / arrows   move the cursor -- updates the panes to the right live
  tab / shift+tab  switch focus between the categories and topics tables
  left / right     switch focus between the categories and topics tables
  enter            switch focus (categories <-> topics)
  esc              focus categories (or clear the filter)
  s                cycle the focused table's sort order
  ?                this help

Press any key to close.
"""


class NavDataTable(DataTable):
    """A DataTable with vim-style j/k cursor movement alongside the built-in arrow keys."""

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]
