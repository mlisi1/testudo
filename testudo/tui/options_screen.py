"""The Options menu (`o`) and its screens.

Split out of screens.py (which stays focused on the live dashboard + help
overlay) since this is a separate, independently-growing concern: a
top-level `OptionsMenuScreen` listing settings to drill into, and one
screen per setting -- today just `ExcludeTopicsScreen`. A future setting
gets its own screen alongside it and one more entry in the menu, rather
than either screen accumulating unrelated widgets of its own.
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from testudo.core.config import ExcludeMatchType, ExcludeRule, InvalidExcludePatternError
from testudo.tui.data_source import WatchDataSource
from testudo.tui.keybinds import NavDataTable

#: Case-insensitive prefixes marking a pattern as a regex, stripped before
#: constructing the `ExcludeRule` -- one field, one convention, rather
#: than a separate Literal/Regex `RadioSet` a user has to notice, focus
#: (Tab away from the pattern `Input` first), and remember to flip before
#: submitting. That widget shipped first and proved exactly that easy to
#: miss in practice -- a submission left on its "Literal" default with a
#: pattern the user clearly meant as a regex, twice.
_REGEX_INPUT_PREFIXES = ("regex:", "re:")


def _parse_pattern_input(raw: str) -> tuple[str, ExcludeMatchType]:
    """Split one Options-screen input into (pattern, match_type) by its `regex:`/`re:` prefix."""
    stripped = raw.strip()
    lower = stripped.lower()
    for prefix in _REGEX_INPUT_PREFIXES:
        if lower.startswith(prefix):
            return stripped[len(prefix) :].strip(), ExcludeMatchType.REGEX
    return stripped, ExcludeMatchType.LITERAL


class OptionsMenuScreen(ModalScreen):
    """`o`: the top-level options menu -- a list of settings to drill into.

    Currently just one entry (Exclude Topics); this screen's only job is
    to list entries and push the screen behind whichever one is selected.
    `Esc` from a pushed setting screen pops back to this menu for free
    (it's just the screen below on the stack again), and `Esc` from here
    closes the whole thing.
    """

    BINDINGS = [Binding("escape", "dismiss_screen", "Close")]

    _EXCLUDE_TOPICS_OPTION_ID = "exclude_topics"

    def __init__(self, data_source: WatchDataSource) -> None:
        super().__init__()
        self._data_source = data_source

    def compose(self) -> ComposeResult:
        with Vertical(id="options-menu-panel"):
            yield Static("[b]Options[/b]", id="options-menu-title")
            yield OptionList(
                Option("Exclude Topics", id=self._EXCLUDE_TOPICS_OPTION_ID),
                id="options-menu-list",
            )
            yield Static("enter: open   esc: close", id="options-menu-hint")

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    def action_dismiss_screen(self) -> None:
        self.dismiss()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_id == self._EXCLUDE_TOPICS_OPTION_ID:
            self.app.push_screen(ExcludeTopicsScreen(self._data_source))


class ExcludeTopicsScreen(ModalScreen):
    """Options > Exclude Topics: add/remove `exclude_topics` rules live.

    Backed by whatever `WatchDataSource` the app has: a live session's
    rule takes effect immediately (tearing down any already-subscribed
    topic it newly matches) and is written straight back to the config
    file that was loaded at startup, so it survives a restart; a replay's
    `StaticDataSource` shows its config's rules read-only, since there's
    nothing live to unsubscribe and no file to persist into.
    """

    BINDINGS = [
        Binding("escape", "dismiss_screen", "Close"),
        Binding("a", "focus_add", "Add rule"),
        Binding("d", "delete_selected", "Delete"),
    ]

    def __init__(self, data_source: WatchDataSource) -> None:
        super().__init__()
        self._data_source = data_source
        self._rows: list[ExcludeRule] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="options-panel"):
            yield Static("[b]Exclude Topics[/b]", id="options-title")
            yield NavDataTable(id="exclude-table")
            yield Input(placeholder="topic name, or regex:<pattern>", id="exclude-pattern-input")
            yield Static("", id="options-preview")
            yield Static(self._hint_text(), id="options-message")

    def _hint_text(self) -> str:
        if self._data_source.is_live():
            return "a: add   d: delete selected   enter: submit   'regex:' prefix for a regex   esc: back"
        return "[dim]replay is read-only -- rules shown are the config this replay used[/dim]"

    def on_mount(self) -> None:
        table = self.query_one("#exclude-table", NavDataTable)
        table.cursor_type = "row"
        table.add_column("Pattern", key="pattern", width=34)
        table.add_column("Type", key="type", width=10)
        self._refresh_table()
        table.focus()

    def _refresh_table(self) -> None:
        table = self.query_one("#exclude-table", NavDataTable)
        table.clear()
        self._rows = self._data_source.exclude_rules()
        for rule in self._rows:
            table.add_row(rule.pattern, rule.match_type.value)

    def action_dismiss_screen(self) -> None:
        self.dismiss()

    def action_focus_add(self) -> None:
        self.query_one("#exclude-pattern-input", Input).focus()

    def _update_preview(self) -> None:
        """Show, live as the user types, how many *currently visible* topics the
        rule-in-progress would actually match -- before it's ever submitted.

        This is what would have caught "/filter" saved as a literal rule
        (matches only a topic named exactly "/filter", almost certainly
        none) instead of the regex the user actually wanted: silently
        "successful" but functionally a no-op, with nothing in the UI to
        suggest anything was wrong until they went looking for a topic
        that never disappeared.
        """
        preview = self.query_one("#options-preview", Static)
        raw = self.query_one("#exclude-pattern-input", Input).value
        pattern, match_type = _parse_pattern_input(raw)
        if not pattern:
            preview.update("")
            return
        try:
            rule = ExcludeRule(pattern=pattern, match_type=match_type)
        except InvalidExcludePatternError as exc:
            preview.update(f"[red]{exc}[/red]")
            return
        kind = "regex" if match_type is ExcludeMatchType.REGEX else "literal"
        topics = {report.topic for report in self.app.latest_snapshot.reports}
        matched = sorted(name for name in topics if rule.matches(name))
        if not matched:
            preview.update(f"[yellow]{kind}: matches 0 currently-visible topic(s)[/yellow]")
            return
        shown = ", ".join(matched[:3])
        more = f" (+{len(matched) - 3} more)" if len(matched) > 3 else ""
        preview.update(f"[green]{kind}: matches {len(matched)} currently-visible topic(s):[/green] {shown}{more}")

    def on_input_changed(self, event: Input.Changed) -> None:
        self._update_preview()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        message = self.query_one("#options-message", Static)
        pattern, match_type = _parse_pattern_input(event.value)
        if not pattern:
            return
        if not self._data_source.is_live():
            message.update("[yellow]replay is read-only -- can't add exclude rules here[/yellow]")
            return
        try:
            rule = ExcludeRule(pattern=pattern, match_type=match_type)
        except InvalidExcludePatternError as exc:
            message.update(f"[red]{exc}[/red]")
            return

        affected = self._data_source.add_exclude_rule(rule)
        event.input.value = ""
        if affected is None:
            message.update("[yellow]that rule is already in effect[/yellow]")
        else:
            self._refresh_table()
            suffix = f", will drop {affected} already-tracked topic(s) shortly" if affected else ""
            message.update(f"[green]added -- saved to config{suffix}[/green]")
        # Submitting an Input doesn't move focus off it -- left here, the
        # very next keystroke (e.g. 'd' to delete what was just added)
        # would just get typed into this now-empty field instead of
        # triggering the table's binding, forcing a close/reopen to get
        # focus back onto the table. Refocusing it here is what makes
        # add-then-immediately-delete work in one continuous flow.
        self.query_one("#exclude-table", NavDataTable).focus()

    def action_delete_selected(self) -> None:
        message = self.query_one("#options-message", Static)
        if not self._data_source.is_live():
            message.update("[yellow]replay is read-only -- can't remove exclude rules here[/yellow]")
            return
        table = self.query_one("#exclude-table", NavDataTable)
        if table.cursor_row is None or table.cursor_row < 0 or table.cursor_row >= len(self._rows):
            return
        rule = self._rows[table.cursor_row]
        self._data_source.remove_exclude_rule(rule)
        self._refresh_table()
        message.update("[green]removed -- saved to config[/green]")
