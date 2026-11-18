"""Options > Checks and Options > Display: a list of settings, Enter or Space moving one to its next value.

Each change applies at once and is saved: Checks to the robot's config,
Display to the per-user preferences file.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from testudo.core.live_settings import CHECK_SETTINGS
from testudo.tui.display_prefs import DISPLAY_SETTINGS, DisplaySetting

if TYPE_CHECKING:
    from testudo.tui.app import TestudoApp
    from testudo.tui.data_source import LiveDataSource

_LABEL_WIDTH = 20

_VALUE_NAMES = {"worst_first": "worst first", "name": "by name"}


@dataclass(frozen=True)
class SettingRow:
    """One line of the list."""

    key: str
    label: str
    value: Any
    choices: tuple[Any, ...]
    help: str = ""


class SettingsSource(Protocol):
    """Where a list of settings comes from, and what changing one does."""

    title: str
    hint: str

    def rows(self) -> list[SettingRow]: ...

    def change(self, key: str, value: Any) -> str: ...


class ChecksSettings:
    """How the running checks behave, saved to the robot's config."""

    title = "Checks"
    hint = "saved to the robot's config"

    def __init__(self, source: "LiveDataSource") -> None:
        self._source = source

    def rows(self) -> list[SettingRow]:
        values = self._source.checks.values()
        return [SettingRow(item.key, item.label, values[item.key], item.choices, item.help) for item in CHECK_SETTINGS]

    def change(self, key: str, value: Any) -> str:
        return self._source.change_check(key, value)


class DisplaySettings:
    """How this user likes the dashboard, saved to their own preferences file."""

    title = "Display"
    hint = "saved for this user only"

    def __init__(self, app: "TestudoApp") -> None:
        self._app = app

    def rows(self) -> list[SettingRow]:
        prefs = self._app.display_prefs
        return [
            SettingRow(item.key, item.label, getattr(prefs, item.key), item.choices, self._help(item))
            for item in DISPLAY_SETTINGS
        ]

    def _help(self, item: DisplaySetting) -> str:
        if item.key == "refresh_hz" and self._app.display_prefs.refresh_hz > self._app.poll_rate_hz:
            return f"{item.help}\nNew data arrives at {self._app.poll_rate_hz:g} Hz, so it can't draw faster."
        return item.help

    def change(self, key: str, value: Any) -> str:
        return self._app.change_display_pref(key, value)


def next_choice(choices: tuple[Any, ...], current: Any) -> Any:
    """The value after `current`; from a value not on the list (set by hand), the next larger one."""
    if current in choices:
        return choices[(choices.index(current) + 1) % len(choices)]
    if isinstance(current, (int, float)) and not isinstance(current, bool):
        return next((choice for choice in choices if choice > current), choices[0])
    return choices[0]


def format_value(value: Any) -> str:
    """A setting's value as the list shows it."""
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, float):
        return f"{value:g}"
    return _VALUE_NAMES.get(value, str(value))


def row_text(row: SettingRow) -> str:
    """One aligned line of the list."""
    return f"{row.label:<{_LABEL_WIDTH}}{format_value(row.value)}"


class SettingsScreen(ModalScreen):
    """A list of settings; `Esc` goes back to the Options menu."""

    BINDINGS = [
        Binding("escape", "dismiss_screen", "Back"),
        Binding("space", "change_highlighted", "Change"),
    ]

    def __init__(self, settings: SettingsSource) -> None:
        super().__init__()
        self._settings = settings

    def compose(self) -> ComposeResult:
        with Vertical(id="settings-panel"):
            yield Static(f"[b]{self._settings.title}[/b]", id="options-title")
            with Horizontal(id="settings-body"):
                yield OptionList(*(Option(row_text(row), id=row.key) for row in self._settings.rows()), id="settings-list")
                yield Static("", id="settings-help", markup=False)
            yield Static(f"enter/space: change   esc: back   ({self._settings.hint})", id="options-message")

    def on_mount(self) -> None:
        settings_list = self.query_one(OptionList)
        settings_list.highlighted = 0
        settings_list.focus()

    def action_dismiss_screen(self) -> None:
        self.dismiss()

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        self._show_help(event.option_index)

    def _show_help(self, index: int) -> None:
        self.query_one("#settings-help", Static).update(self._settings.rows()[index].help)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self._change(event.option_index)

    def action_change_highlighted(self) -> None:
        highlighted = self.query_one(OptionList).highlighted
        if highlighted is not None:
            self._change(highlighted)

    def _change(self, index: int) -> None:
        row = self._settings.rows()[index]
        outcome = self._settings.change(row.key, next_choice(row.choices, row.value))
        settings_list = self.query_one(OptionList)
        for position, updated in enumerate(self._settings.rows()):
            settings_list.replace_option_prompt_at_index(position, row_text(updated))
        self._show_help(index)
        color = "yellow" if "session only" in outcome or "next start" in outcome else "green"
        self.query_one("#options-message", Static).update(f"[{color}]{outcome}[/{color}]")
