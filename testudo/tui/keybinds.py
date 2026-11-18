"""Shared keybindings and the vim-nav DataTable subclass every panel uses.

Textual's DataTable already binds the arrow keys to cursor movement; this
module only adds the vim-style aliases and documents the rest of the
project's keybind set (`q` quit, `p` pause, `/` filter, `j`/`k`/arrows/Tab/
Left/Right navigate, `Enter` switch pane, `Esc` back to categories (or
clear the filter), `s` toggle sort, `y` copy topic name, `n`/`N` next/previous
problem, `a` quick actions, `r` reset stats, `o` options menu, `?`
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
    # replay --watch only (no-ops otherwise): step through the bag's frames.
    Binding("left_square_bracket", "step_frames(-1)", "Back a frame", key_display="["),
    Binding("right_square_bracket", "step_frames(1)", "Forward a frame", key_display="]"),
    Binding("left_curly_bracket", "step_frames(-10)", "Back 10 frames", key_display="{"),
    Binding("right_curly_bracket", "step_frames(10)", "Forward 10 frames", key_display="}"),
]

#: Screen-level bindings (only meaningful where there's a table to filter/sort).
FILTER_BINDING = Binding("slash", "start_filter", "Filter", key_display="/")
SORT_BINDING = Binding("s", "toggle_sort", "Sort")
#: Copies the highlighted topic's full name (the real topic, e.g. an
#: action's `/_action/status`, not its shortened display name), ready to
#: paste into `ros2 topic echo`. Via OSC 52, so it works over SSH too.
COPY_BINDING = Binding("y", "copy_topic", "Copy topic")

#: Jump to the next/previous topic with an active code, in "Needs attention"
#: order (`attention.problem_topics`), across categories.
NEXT_PROBLEM_BINDING = Binding("n", "jump_problem(1)", "Next problem")
PREVIOUS_PROBLEM_BINDING = Binding("N", "jump_problem(-1)", "Previous problem", key_display="N")

#: Opens the highlighted topic's quick actions in the hint bar (`tui/quick_actions.py`).
QUICK_ACTIONS_BINDING = Binding("a", "quick_actions", "Actions")

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

#: Width of the help overlay's key column.
_HELP_KEY_WIDTH = 17


def _help_row(keys: str, description: str) -> str:
    """One help line: `keys` in bold (a literal `[` escaped from markup), then what they do."""
    shown = keys.replace("[", "\\[")
    return f"  [b]{shown}[/b]{' ' * (_HELP_KEY_WIDTH - len(keys))}{description}"


def _help_detail(*actions: tuple[str, str]) -> str:
    """A line under a key's row: the keys available once it's pressed, each in bold."""
    return " " * (_HELP_KEY_WIDTH + 4) + " · ".join(f"[b]{key}[/b] {text}" for key, text in actions)


HELP_TEXT = "\n".join(
    [
        "[b]Testudo -- keybindings[/b]",
        "",
        _help_row("q", "quit"),
        _help_row("p", "pause the live view (replay --watch: play / pause)"),
        _help_row("[ / ]", "replay --watch: back / forward a frame ([b]{ / }[/b]: ten)"),
        _help_row("r", "reset stats (clear history, keep watching)"),
        _help_row("o", "options: exclude topics, checks, display"),
        _help_row("/", "search all topics (on the Topic Panel: this category)"),
        _help_row("j / k / arrows", "move the cursor, the other panes follow"),
        _help_row("tab / shift+tab", "switch focus between the categories and topics tables"),
        _help_row("left / right", "switch focus between the categories and topics tables"),
        _help_row("enter", "switch focus (categories <-> topics)"),
        _help_row("esc", "focus categories (or clear the filter)"),
        _help_row("s", "sort the focused table: worst first ▼ / by name ▲"),
        _help_row("y", "copy the highlighted topic's name to the clipboard"),
        _help_row("n / N", "jump to the next / previous topic with a problem"),
        _help_row("a", "actions on this topic, saved to the config:"),
        _help_detail(("w", "downgrade to warn"), ("m", "mute"), ("r", "restore")),
        _help_detail(("l", "warn at the current value"), ("x", "exclude the topic")),
        _help_detail(("c", "next code"), ("esc", "cancel")),
        _help_row("?", "this help"),
        "",
        "Press any key to close.",
        "",
    ]
)


class NavDataTable(DataTable):
    """A DataTable with vim-style j/k cursor movement alongside the built-in arrow keys."""

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]
