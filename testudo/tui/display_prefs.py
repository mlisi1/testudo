"""How this user likes the dashboard shown: a small per-user file, kept out of the robot's config.

The robot config is shared (a repository, a fleet), while refresh rate and
sort order are one person's taste. A broken file must never keep a
diagnostics tool from starting in the field, so anything unusable is logged
and left at its default.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

import yaml

from testudo.core.config import default_config_path
from testudo.core.config_editor import set_value
from testudo.core.config_writer import edit_config_file
from testudo.core.topic_report import TopicReport
from testudo.plugins.base import Severity

_logger = logging.getLogger(__name__)

WORST_FIRST = "worst_first"
BY_NAME = "name"

#: Rows that "hide OK topics" leaves alone: a process isn't a topic, and an excluded topic was never judged OK.
_NEVER_HIDDEN_TIERS = frozenset({"node", "excluded"})


@dataclass(frozen=True)
class DisplayPrefs:
    """The dashboard's display preferences."""

    refresh_hz: float = 5.0
    default_sort: str = WORST_FIRST
    hide_ok_topics: bool = False
    show_nodes: bool = True


@dataclass(frozen=True)
class DisplaySetting:
    """One preference on Options > Display: what it may be set to, and what it does."""

    key: str
    label: str
    choices: tuple[Any, ...]
    help: str


DISPLAY_SETTINGS = (
    DisplaySetting(
        "refresh_hz",
        "Refresh rate (Hz)",
        (1.0, 2.0, 5.0, 10.0),
        "How often the dashboard redraws. Lower is lighter over SSH. Checks and published diagnostics are not affected.",
    ),
    DisplaySetting(
        "default_sort",
        "Default sort",
        (WORST_FIRST, BY_NAME),
        "How the panels are ordered when Testudo starts. The s key still switches the focused panel.",
    ),
    DisplaySetting(
        "hide_ok_topics",
        "Hide OK topics",
        (False, True),
        "Lists only topics with a problem in the Topic Panel. Categories keep their counts.",
    ),
    DisplaySetting(
        "show_nodes",
        "Show Nodes category",
        (True, False),
        "Shows node profiling in the panels. Hiding it doesn't stop the sampling: that's under Checks.",
    ),
)


def default_display_prefs_path() -> Path:
    """`display.yaml` beside the default config: `~/.config/testudo/display.yaml`."""
    return default_config_path().parent / "display.yaml"


def _usable(key: str, value: Any) -> bool:
    if key == "refresh_hz":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0
    if key == "default_sort":
        return value in (WORST_FIRST, BY_NAME)
    return isinstance(value, bool)


def load_display_prefs(path: Path) -> DisplayPrefs:
    """The preferences in `path`; defaults for a missing file and for anything unusable in it."""
    try:
        raw = yaml.safe_load(path.read_text())
    except FileNotFoundError:
        return DisplayPrefs()
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        _logger.warning("ignoring display preferences in %s: %s", path, exc)
        return DisplayPrefs()
    if raw is None:
        return DisplayPrefs()
    if not isinstance(raw, dict):
        _logger.warning("ignoring display preferences in %s: not a mapping", path)
        return DisplayPrefs()
    known = {field.name for field in fields(DisplayPrefs)}
    for key in sorted(set(map(str, raw)) - known):
        _logger.warning("ignoring unknown display preference '%s' in %s", key, path)
    prefs = {}
    for key in known & set(raw):
        if _usable(key, raw[key]):
            prefs[key] = float(raw[key]) if key == "refresh_hz" else raw[key]
        else:
            _logger.warning("ignoring display preference %s: %r in %s", key, raw[key], path)
    return DisplayPrefs(**prefs)


def save_display_pref(path: Path, key: str, value: Any) -> None:
    """Write one preference to `path`, leaving the rest of the file as it is."""
    edit_config_file(path, lambda text: set_value(text, (key,), value))


def visible_reports(reports: list[TopicReport], prefs: DisplayPrefs) -> list[TopicReport]:
    """`reports` without the Nodes category when it's switched off."""
    return reports if prefs.show_nodes else [report for report in reports if report.tier != "node"]


def shows_topic(report: TopicReport, prefs: DisplayPrefs) -> bool:
    """Whether the Topic Panel lists `report`."""
    return (
        not prefs.hide_ok_topics
        or report.status.severity != Severity.OK
        or report.tier in _NEVER_HIDDEN_TIERS
    )
