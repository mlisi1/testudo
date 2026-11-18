"""YAML loader/validator for the typed config schema in `config`.

Every section is parsed and validated here, once, so a malformed config
fails loudly and specifically at load time.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from testudo.core.config import (
    DEFAULT_DEBOUNCE_SAMPLES,
    DEFAULT_MAX_CHECK_RATE_HZ,
    ActionConfig,
    ConfigError,
    ExcludeRule,
    FRAME_ROLE_MSG_TYPES,
    FRAME_ROLES,
    LevelsOverride,
    SeverityMode,
    TestudoConfig,
    TFPairConfig,
    TopicConfig,
)
from testudo.core.config_sections import parse_faults, parse_node_profiling, parse_publish
from testudo.core.config_values import (
    parse_code_overrides,
    parse_exclude_rules,
    parse_metric,
    parse_positive_number,
    parse_topic_name,
    parse_weight,
)
from testudo.plugins.base import ThresholdZone


def load_config(path: str | Path) -> TestudoConfig:
    """Load and validate a Testudo YAML config file.

    Raises:
        ConfigError: the file is missing, is not valid YAML, or fails schema validation.
    """
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: top level must be a mapping, got {type(raw).__name__}")
    return _parse_config(raw, source=str(path))


_TOP_LEVEL_KEYS = {
    "topics",
    "actions",
    "tf",
    "severity_mode",
    "publish",
    "plugins_dir",
    "exclude_topics",
    "node_profiling",
    "faults",
    "check_undeclared_topics",
    "max_check_rate_hz",
    "debounce_samples",
}


def _parse_config(raw: dict[str, Any], source: str) -> TestudoConfig:
    unknown = set(raw) - _TOP_LEVEL_KEYS
    if unknown:
        raise ConfigError(f"{source}: unknown top-level key(s): {', '.join(sorted(unknown))}")

    plugins_dir = raw.get("plugins_dir")
    if plugins_dir is not None and not isinstance(plugins_dir, str):
        raise ConfigError(f"{source}: 'plugins_dir' must be a string")

    check_undeclared_topics = raw.get("check_undeclared_topics", True)
    if not isinstance(check_undeclared_topics, bool):
        raise ConfigError(f"{source}: 'check_undeclared_topics' must be true or false")

    debounce_samples = raw.get("debounce_samples", DEFAULT_DEBOUNCE_SAMPLES)
    if isinstance(debounce_samples, bool) or not isinstance(debounce_samples, int) or debounce_samples < 1:
        raise ConfigError(f"{source}: 'debounce_samples' must be a whole number of at least 1")

    publish = parse_publish(raw.get("publish", {}), source)
    return TestudoConfig(
        topics=_parse_topics(raw.get("topics", {}), source),
        actions=_parse_actions(raw.get("actions", []), source),
        tf=_parse_tf(raw.get("tf", []), source),
        severity_mode=_parse_severity_mode(raw.get("severity_mode", "worst"), source),
        publish=publish,
        plugins_dir=plugins_dir,
        exclude_topics=_parse_exclude_topics(raw.get("exclude_topics", []), source),
        node_profiling=parse_node_profiling(raw.get("node_profiling", {}), source, publish),
        faults=parse_faults(raw.get("faults", {}), source, publish),
        check_undeclared_topics=check_undeclared_topics,
        max_check_rate_hz=parse_positive_number(
            raw.get("max_check_rate_hz", DEFAULT_MAX_CHECK_RATE_HZ), "'max_check_rate_hz'", source
        ),
        debounce_samples=debounce_samples,
    )


def _parse_exclude_topics(raw: Any, source: str) -> list[ExcludeRule]:
    return parse_exclude_rules(raw, source, key="exclude_topics")


def _parse_topics(raw: Any, source: str) -> dict[str, list[TopicConfig]]:
    if not isinstance(raw, dict):
        raise ConfigError(f"{source}: 'topics' must be a mapping of msg type -> topic list")
    result: dict[str, list[TopicConfig]] = {}
    for msg_type, entries in raw.items():
        if not isinstance(msg_type, str):
            raise ConfigError(f"{source}: topic msg type keys must be strings, got {msg_type!r}")
        if not isinstance(entries, list):
            raise ConfigError(f"{source}: topics.{msg_type} must be a list of topic entries")
        result[msg_type] = [_parse_topic_entry(entry, msg_type, source) for entry in entries]
    _refuse_duplicates([entry.name for entries in result.values() for entry in entries], "topics", source)
    return result


def _refuse_duplicates(names: list[str], section: str, source: str) -> None:
    """One entry per name: with two, one would silently replace the other."""
    repeated = sorted({name for name in names if names.count(name) > 1})
    if repeated:
        raise ConfigError(f"{source}: {section} declares more than once: {', '.join(repeated)}")


_TOPIC_ENTRY_KEYS = {"name", "weight", "thresholds", "codes", "related_topics", "event_driven", "frame_role"}


def _parse_topic_entry(entry: Any, msg_type: str, source: str) -> TopicConfig:
    if not isinstance(entry, dict):
        raise ConfigError(f"{source}: topics.{msg_type} entries must be mappings")
    unknown = set(entry) - _TOPIC_ENTRY_KEYS
    if unknown:
        raise ConfigError(f"{source}: topics.{msg_type} entry has unknown key(s): {', '.join(sorted(unknown))}")
    name = entry.get("name")
    if not isinstance(name, str) or not name:
        raise ConfigError(f"{source}: topics.{msg_type} entry missing required string 'name'")
    parse_topic_name(name, f"topics.{msg_type} entry name", source)
    weight = parse_weight(entry.get("weight", 1.0), f"topics.{msg_type}[{name}]", source)
    thresholds, levels = _parse_thresholds(entry.get("thresholds", {}), f"topics.{msg_type}[{name}]", source)
    codes = parse_code_overrides(entry.get("codes", {}), f"topics.{msg_type}[{name}]", source)
    related_topics = _parse_related_topics(entry.get("related_topics", {}), f"topics.{msg_type}[{name}]", source)
    event_driven = entry.get("event_driven", False)
    if not isinstance(event_driven, bool):
        raise ConfigError(f"{source}: topics.{msg_type}[{name}].event_driven must be true or false")
    frame_role = entry.get("frame_role")
    if frame_role is not None and (not isinstance(frame_role, str) or frame_role not in FRAME_ROLES):
        raise ConfigError(f"{source}: topics.{msg_type}[{name}].frame_role must be one of: {', '.join(sorted(FRAME_ROLES))}")
    if frame_role is not None and msg_type not in FRAME_ROLE_MSG_TYPES:
        raise ConfigError(f"{source}: topics.{msg_type}[{name}].frame_role only applies to: {', '.join(sorted(FRAME_ROLE_MSG_TYPES))}")
    return TopicConfig(
        name=name,
        msg_type=msg_type,
        weight=weight,
        thresholds=thresholds,
        levels=levels,
        codes=codes,
        related_topics=related_topics,
        event_driven=event_driven,
        frame_role=frame_role,
    )


def _parse_related_topics(raw: Any, context: str, source: str) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise ConfigError(f"{source}: {context}.related_topics must be a mapping of logical name -> topic name")
    result: dict[str, str] = {}
    for logical_name, topic_name in raw.items():
        if not isinstance(logical_name, str) or not logical_name:
            raise ConfigError(f"{source}: {context}.related_topics has a non-string or empty key")
        result[logical_name] = parse_topic_name(topic_name, f"{context}.related_topics.{logical_name}", source)
    return result


def _parse_thresholds(
    raw: Any, context: str, source: str
) -> tuple[dict[str, ThresholdZone], dict[str, LevelsOverride]]:
    """A `thresholds:` block as (graphic zones, severity levels), each keyed by metric."""
    if not isinstance(raw, dict):
        raise ConfigError(f"{source}: {context}.thresholds must be a mapping")
    zones: dict[str, ThresholdZone] = {}
    levels: dict[str, LevelsOverride] = {}
    for metric, entry in raw.items():
        zone, level = parse_metric(entry, f"{context}.thresholds.{metric}", source)
        if zone is not None:
            zones[metric] = zone
        if level is not None:
            levels[metric] = level
    return zones, levels


_ACTION_ENTRY_KEYS = {"name", "weight", "thresholds", "codes", "related_topics"}


def _parse_actions(raw: Any, source: str) -> list[ActionConfig]:
    if not isinstance(raw, list):
        raise ConfigError(f"{source}: 'actions' must be a list")
    result = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ConfigError(f"{source}: actions entries must be mappings")
        unknown = set(entry) - _ACTION_ENTRY_KEYS
        if unknown:
            raise ConfigError(f"{source}: action entry has unknown key(s): {', '.join(sorted(unknown))}")
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            raise ConfigError(f"{source}: action entry missing required string 'name'")
        weight = parse_weight(entry.get("weight", 1.0), f"actions[{name}]", source)
        thresholds, levels = _parse_thresholds(entry.get("thresholds", {}), f"actions[{name}]", source)
        codes = parse_code_overrides(entry.get("codes", {}), f"actions[{name}]", source)
        related_topics = _parse_related_topics(entry.get("related_topics", {}), f"actions[{name}]", source)
        result.append(
            ActionConfig(
                name=name,
                weight=weight,
                thresholds=thresholds,
                levels=levels,
                codes=codes,
                related_topics=related_topics,
            )
        )
    _refuse_duplicates([action.name.lstrip("/") for action in result], "actions", source)
    return result


def _parse_tf(raw: Any, source: str) -> list[TFPairConfig]:
    if not isinstance(raw, list):
        raise ConfigError(f"{source}: 'tf' must be a list")
    result = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ConfigError(f"{source}: tf entries must be mappings")
        unknown = set(entry) - {"parent", "child"}
        if unknown:
            raise ConfigError(f"{source}: tf entry has unknown key(s): {', '.join(sorted(map(str, unknown)))}")
        parent = entry.get("parent")
        if not isinstance(parent, str) or not parent:
            raise ConfigError(f"{source}: tf entry missing required string 'parent'")
        child = entry.get("child")
        if not isinstance(child, str) or not child:
            raise ConfigError(f"{source}: tf entry '{parent}' missing required string 'child'")
        result.append(TFPairConfig(parent=parent, child=child))
    _refuse_duplicates([f"{pair.parent} -> {pair.child}" for pair in result], "tf", source)
    return result


def _parse_severity_mode(raw: Any, source: str) -> SeverityMode:
    if not isinstance(raw, str):
        raise ConfigError(f"{source}: 'severity_mode' must be a string")
    try:
        return SeverityMode(raw)
    except ValueError:
        valid = ", ".join(mode.value for mode in SeverityMode)
        raise ConfigError(f"{source}: 'severity_mode' must be one of [{valid}], got {raw!r}") from None
