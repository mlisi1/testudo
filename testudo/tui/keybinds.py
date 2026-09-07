"""Shared keybindings and the vim-nav DataTable subclass every panel uses.

Textual's DataTable already binds the arrow keys to cursor movement; this
module only adds the vim-style aliases and documents the rest of the
project's keybind set (`q` quit, `p` pause, `/` filter, `j`/`k`/arrows
navigate, `Enter` drill down, `Esc` back, `s` cycle sort, `r` reset stats,
`?` help overlay) in one place other modules can point at.
"""
from __future__ import annotations

from textual.binding import Binding
from textual.widgets import DataTable

#: Global bindings, active regardless of which screen is on top.
APP_BINDINGS = [
    Binding("q", "quit", "Quit"),
    Binding("p", "toggle_pause", "Pause"),
    Binding("r", "reset_stats", "Reset stats"),
    Binding("question_mark", "show_help", "Help", key_display="?"),
]

#: Screen-level bindings (only meaningful where there's a table to filter/sort).
FILTER_BINDING = Binding("slash", "start_filter", "Filter", key_display="/")
SORT_BINDING = Binding("s", "cycle_sort", "Sort")

HELP_TEXT = """\
[b]Testudo -- keybindings[/b]

  q          quit
  p          pause / resume the live view
  r          reset stats (clear accumulated history, keep watching)
  /          filter the current table by name
  j / k / arrows   move the cursor
  enter      drill down
  esc        back (or clear the filter)
  s          cycle sort order
  ?          this help

Press any key to close.
"""


class NavDataTable(DataTable):
    """A DataTable with vim-style j/k cursor movement alongside the built-in arrow keys."""

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]
