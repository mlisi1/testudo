"""Detail Panel values: which of a plugin's value variants to show, and their keys made readable.

A plugin emits plain snake_case `CheckStatus.values`, with optional
`_compact` (narrow pane) and `_more` (tall pane) variants; this picks the
variants that fit the pane and humanizes each pair for display only
(`time_in_status_s: 12.4` -> `time in status  12.4 s`). The grid layout
that places them is `detail_render`'s.
"""
from __future__ import annotations

from testudo.plugins.base import COMPACT_VALUE_SUFFIX, MORE_VALUE_SUFFIX

#: Key suffix -> unit shown after the value instead of inside the label.
#: Longest suffix first, so "_mps" is tried before "_s" would ever match.
_UNIT_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("_per_sec", "/s"),
    ("_per_min", "/min"),
    ("_percent", "%"),
    ("_mps", "m/s"),
    ("_mb", "MB"),
    ("_hz", "Hz"),
    ("_s", "s"),
    ("_m", "m"),
)

#: Label words shown with their conventional capitalization rather than as written in the key.
_ACRONYMS = {"cpu": "CPU", "gpu": "GPU", "pid": "PID", "psd": "PSD", "fds": "FDs", "id": "ID", "tf": "TF"}

#: Values that mean "nothing to show here", dimmed so real readings stand out.
_PLACEHOLDER_VALUES = frozenset({"n/a", "off", "none", "warming up", "-"})


def resolve_values(values: dict[str, str], is_narrow: bool, is_tall: bool = False) -> list[tuple[str, str]]:
    """Collapse `<key>`/`<key>_compact` pairs in `values` down to one entry each.

    Preserves `values`' insertion order. A key with no compact counterpart
    (most of them) passes through unchanged, from either side of the pair.
    A `<key>_more` entry is kept as `<key>` only when `is_tall`, and
    dropped otherwise.
    """
    return [
        (key[: -len(MORE_VALUE_SUFFIX)], value) if key.endswith(MORE_VALUE_SUFFIX) else (key, value)
        for key, value in _resolve_compact(values, is_narrow)
        if is_tall or not key.endswith(MORE_VALUE_SUFFIX)
    ]


def _resolve_compact(values: dict[str, str], is_narrow: bool) -> list[tuple[str, str]]:
    resolved: list[tuple[str, str]] = []
    skip: set[str] = set()
    for key, value in values.items():
        if key in skip:
            continue
        if key.endswith(COMPACT_VALUE_SUFFIX):
            base = key[: -len(COMPACT_VALUE_SUFFIX)]
            skip.add(base)
            if is_narrow:
                resolved.append((base, value))
            continue
        compact_key = f"{key}{COMPACT_VALUE_SUFFIX}"
        if compact_key in values:
            skip.add(compact_key)
            resolved.append((key, values[compact_key] if is_narrow else value))
            continue
        resolved.append((key, value))
    return resolved


def _label(key: str) -> str:
    return " ".join(_ACRONYMS.get(word, word) for word in key.split("_"))


def _is_number(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


def _split_unit(key: str) -> tuple[str, str]:
    """`("time_in_status", "s")` for `time_in_status_s`; `(key, "")` when no unit suffix applies."""
    for suffix, unit in _UNIT_SUFFIXES:
        if key.endswith(suffix) and len(key) > len(suffix):
            return key[: -len(suffix)], unit
    return key, ""


def humanize_pairs(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Display `(label, value)` for each `(key, value)`: spaces for underscores, units on the value.

    A key ending in a known unit suffix (`_s`, `_mb`, `_hz`, `_per_sec`,
    ...) drops it from the label and, when the value is a plain number,
    gains it on the value instead: `memory_mb: 640.0` -> `memory  640.0
    MB`. A non-numeric value ("n/a", "warming up") is left bare. Where
    dropping the suffix would leave two entries sharing one label
    (`memory_mb`/`memory_percent`, or a count next to its own
    `_per_sec` rate), the unit stays in the label instead -- `memory
    (MB)`, `memory (%)` -- so they remain distinguishable whatever their
    values are. Values are otherwise passed through untouched, Rich
    markup included.
    """
    stems = [_split_unit(key) for key, _ in pairs]
    label_uses: dict[str, int] = {}
    for stem, _unit in stems:
        label_uses[stem] = label_uses.get(stem, 0) + 1

    humanized = []
    for (key, value), (stem, unit) in zip(pairs, stems):
        label = _label(stem)
        if not unit:
            humanized.append((label, value))
        elif label_uses[stem] > 1:
            humanized.append((f"{label} ({unit})", value))
        else:
            humanized.append((label, f"{value} {unit}" if _is_number(value) else value))
    return humanized


def style_value(value: str) -> str:
    if value == "":
        return "[dim]–[/dim]"
    if value.lower() in _PLACEHOLDER_VALUES:
        return f"[dim]{value}[/dim]"
    return value
