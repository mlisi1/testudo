"""Value-level config parsers shared by every config section: thresholds, exclude rules, numbers, strings.

Each raises `ConfigError` naming the file (`source`) and the key it
failed on, so a malformed config fails loudly at load time (ground rule 5).
The section structure itself is `config_loader`'s.
"""
from __future__ import annotations

import logging
from typing import Any

from testudo.core.config import ConfigError, ExcludeMatchType, ExcludeRule, InvalidExcludePatternError, LevelsOverride
from testudo.plugins.base import Severity, ThresholdZone

_logger = logging.getLogger(__name__)

_ZONE_KEYS = ("green", "orange", "red")
_LEVEL_KEYS = ("warn", "error")


_EXCLUDE_RULE_KEYS = {"pattern", "type"}


def parse_exclude_rules(raw: Any, source: str, key: str) -> list[ExcludeRule]:
    if not isinstance(raw, list):
        raise ConfigError(f"{source}: '{key}' must be a list of patterns")
    return [parse_exclude_rule(entry, source, key) for entry in raw]


def parse_exclude_rule(entry: Any, source: str, key: str = "exclude_topics") -> ExcludeRule:
    """A plain string is shorthand for a literal rule; a mapping can also opt into `type: regex`.

    `key` names the config section this entry came from (`exclude_topics`
    or `exclude_nodes`), so a validation error points at the section the
    user actually wrote rather than always saying "exclude_topics".
    """
    if isinstance(entry, str):
        pattern, match_type = entry, ExcludeMatchType.LITERAL
    elif isinstance(entry, dict):
        unknown = set(entry) - _EXCLUDE_RULE_KEYS
        if unknown:
            raise ConfigError(f"{source}: {key} entry has unknown key(s): {', '.join(sorted(unknown))}")
        pattern = entry.get("pattern")
        match_type = parse_exclude_match_type(entry.get("type", "literal"), source, key)
    else:
        raise ConfigError(f"{source}: {key} entries must be a string or a {{pattern, type}} mapping")
    if not isinstance(pattern, str) or not pattern:
        raise ConfigError(f"{source}: {key} entries must have a non-empty string 'pattern'")
    try:
        return ExcludeRule(pattern=pattern, match_type=match_type)
    except InvalidExcludePatternError as exc:
        raise ConfigError(f"{source}: {key}: {exc}") from exc


def parse_exclude_match_type(raw: Any, source: str, key: str = "exclude_topics") -> ExcludeMatchType:
    if not isinstance(raw, str):
        raise ConfigError(f"{source}: {key} entry 'type' must be a string")
    try:
        return ExcludeMatchType(raw)
    except ValueError:
        valid = ", ".join(t.value for t in ExcludeMatchType)
        raise ConfigError(f"{source}: {key} entry 'type' must be one of [{valid}], got {raw!r}") from None


def parse_metric(raw: Any, context: str, source: str) -> tuple[ThresholdZone | None, LevelsOverride | None]:
    """One metric's thresholds entry: its graphic zone and its severity levels, either optional.

    `{warn: 0.05, error: 1000, green: 0.01, orange: 0.1}` -- warn/error decide
    severity, green/orange/red only color meters. An entry with zones but no
    levels logs a WARNING: before levels existed, green/orange decided severity.
    """
    if not isinstance(raw, dict):
        raise ConfigError(f"{source}: {context} must be a mapping with warn/error and/or green/orange/red")
    unknown = set(raw) - set(_ZONE_KEYS) - set(_LEVEL_KEYS)
    if unknown:
        raise ConfigError(f"{source}: {context} has unknown key(s): {', '.join(sorted(map(str, unknown)))}")
    for key, value in raw.items():
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
            raise ConfigError(f"{source}: {context}.{key} must be numeric (or null)")
    zone = (
        ThresholdZone(green=_to_float(raw.get("green")), orange=_to_float(raw.get("orange")), red=_to_float(raw.get("red")))
        if any(key in raw for key in _ZONE_KEYS)
        else None
    )
    given = frozenset(key for key in _LEVEL_KEYS if key in raw)
    levels = LevelsOverride(warn=_to_float(raw.get("warn")), error=_to_float(raw.get("error")), given=given) if given else None
    if zone is not None and levels is None:
        _logger.warning(
            "%s: %s sets only green/orange/red, which now only color meters; add warn/error to set when it is WARN/ERROR",
            source,
            context,
        )
    return zone, levels


#: What a `codes:` override may set a code to.
CODE_SEVERITY_NAMES: dict[str, int] = {"ok": Severity.OK, "warn": Severity.WARN, "error": Severity.ERROR}


def parse_code_overrides(raw: Any, context: str, source: str) -> dict[str, int]:
    """`codes: {GNSS-001: warn}` as {code: Severity}; codes are upper-cased, severities must be ok/warn/error."""
    if not isinstance(raw, dict):
        raise ConfigError(f"{source}: {context}.codes must be a mapping of code -> ok/warn/error")
    result: dict[str, int] = {}
    for code, name in raw.items():
        if not isinstance(code, str) or not code.strip() or " " in code.strip():
            raise ConfigError(f"{source}: {context}.codes has an invalid code {code!r} (e.g. GNSS-001)")
        severity = CODE_SEVERITY_NAMES.get(name.lower()) if isinstance(name, str) else None
        if severity is None:
            raise ConfigError(
                f"{source}: {context}.codes.{code} must be one of {', '.join(CODE_SEVERITY_NAMES)}, got {name!r}"
            )
        result[code.strip().upper()] = severity
    return result


def _to_float(value: Any) -> float | None:
    return None if value is None else float(value)


def parse_positive_number(value: Any, context: str, source: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ConfigError(f"{source}: {context} must be a positive number")
    return float(value)


def parse_weight(value: Any, context: str, source: str) -> float:
    """A topic's share of the weighted score: 0 leaves it out, negative makes no sense."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ConfigError(f"{source}: {context}.weight must be a number of at least 0")
    return float(value)


def parse_topic_name(value: Any, context: str, source: str) -> str:
    """An absolute topic name: the graph only has those, so `odom` would silently match nothing."""
    if not isinstance(value, str) or not value.startswith("/") or value == "/":
        raise ConfigError(f"{source}: {context} must be an absolute topic name like /odom, got {value!r}")
    return value


def parse_non_empty_string(value: Any, context: str, source: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{source}: {context} must be a non-empty string")
    return value
