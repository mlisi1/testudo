"""The Textual version the dashboard needs, checked before anything imports Textual.

A ROS apt install resolves `python3-textual` to Ubuntu's package, which is far
older than the dashboard's widgets need. The headless and replay commands work
without it, so only the dashboard checks, and says what to install.
"""
from __future__ import annotations

import importlib.metadata
import re

#: The oldest Textual release the whole dashboard test suite passes on.
MIN_TEXTUAL = (6, 0)

INSTALL_COMMAND = "python3 -m pip install --user --break-system-packages 'textual>=6'"


def _release(version: str) -> tuple[int, ...]:
    """The leading numbers of `version`: `6.2.1` -> (6, 2, 1), `7.0.0rc1` -> (7, 0, 0)."""
    numbers = []
    for part in version.split("."):
        match = re.match(r"\d+", part)
        if match is None:
            break
        numbers.append(int(match.group()))
    return tuple(numbers)


def textual_problem(installed: str | None = None) -> str | None:
    """Why the dashboard can't start with the installed Textual, or None when it can.

    `installed` is the Textual version to judge; by default, the one installed.
    """
    if installed is None:
        try:
            installed = importlib.metadata.version("textual")
        except importlib.metadata.PackageNotFoundError:
            installed = ""
    minimum = ".".join(str(number) for number in MIN_TEXTUAL)
    if not installed:
        found = "Textual isn't installed"
    elif _release(installed) < MIN_TEXTUAL:
        found = f"Textual {installed} is installed"
    else:
        return None
    return (
        f"the dashboard needs Textual {minimum} or newer, and {found}. Install it with:\n"
        f"    {INSTALL_COMMAND}\n"
        "`testudo watch --headless` and `testudo replay` work without it."
    )
