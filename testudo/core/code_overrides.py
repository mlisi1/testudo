"""Per-topic code severity overrides (`codes: {GNSS-001: warn}` in config), applied to a finished status.

Applied in core after the plugin and after hysteresis, never inside a
plugin, so they work the same for every plugin, built-in or not, and for
the LIVE-* codes Testudo raises itself. An overridden code takes its new
severity (its message re-colored to match); one overridden to `ok` is
dropped, so it's no fault and no "Needs attention" line either.

The topic's severity is then recomputed from the codes left, but only
when the codes explained it in the first place (the worst code was at
least as severe as the status). A status worse than any of its codes
(a third-party plugin that doesn't code everything) keeps its severity,
and an override can only raise it.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from testudo.core.plain_values import strip_markup
from testudo.plugins.base import CheckStatus, Severity, code_severity, colorize


def apply_code_overrides(status: CheckStatus, overrides: Mapping[str, int]) -> CheckStatus:
    """`status` with each code in `overrides` set to its configured severity (OK drops the code)."""
    applicable = {code: severity for code, severity in overrides.items() if code in status.codes}
    if not applicable:
        return status
    original = {code: code_severity(status, code) for code in status.codes}
    explained = status.severity <= max(original.values())

    codes: dict[str, str] = {}
    severities: dict[str, int] = {}
    for code, message in status.codes.items():
        if code not in applicable:
            codes[code], severities[code] = message, original[code]
        elif applicable[code] > Severity.OK:
            codes[code] = colorize(strip_markup(message), applicable[code])
            severities[code] = applicable[code]

    worst_code = max(severities.values(), default=Severity.OK)
    severity = worst_code if explained else max(status.severity, worst_code)
    return replace(status, severity=severity, codes=codes, code_severities=severities)
