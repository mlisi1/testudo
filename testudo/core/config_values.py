"""Value-level config parsers shared by every config section: zones, exclude rules, numbers, strings.

Each raises `ConfigError` naming the file (`source`) and the key it
failed on, so a malformed config fails loudly at load time (ground rule 5).
The section structure itself is `config_loader`'s.
"""
from __future__ import annotations

from typing import Any

from testudo.core.config import ConfigError, ExcludeMatchType, ExcludeRule, InvalidExcludePatternError
from testudo.plugins.base import ThresholdZone


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


def parse_zone(raw: Any, context: str, source: str) -> ThresholdZone:
    if not isinstance(raw, dict):
        raise ConfigError(f"{source}: {context} must be a mapping with green/orange/red")
    unknown = set(raw) - {"green", "orange", "red"}
    if unknown:
        raise ConfigError(f"{source}: {context} has unknown key(s): {', '.join(sorted(unknown))}")
    for key, value in raw.items():
        if value is not None and not isinstance(value, (int, float)):
            raise ConfigError(f"{source}: {context}.{key} must be numeric")
    return ThresholdZone(green=_to_float(raw.get("green")), orange=_to_float(raw.get("orange")), red=_to_float(raw.get("red")))


def _to_float(value: Any) -> float | None:
    return None if value is None else float(value)


def parse_positive_number(value: Any, context: str, source: str) -> float:
    if not isinstance(value, (int, float)) or value <= 0:
        raise ConfigError(f"{source}: {context} must be a positive number")
    return float(value)


def parse_non_empty_string(value: Any, context: str, source: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{source}: {context} must be a non-empty string")
    return value
