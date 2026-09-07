"""Typed config schema and YAML loader/validator.

Config is parsed into dataclasses once at startup and validated here, so a
malformed config fails loudly and specifically at load time rather than
three modules deep with a KeyError.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from testudo.plugins.base import ThresholdZone


class ConfigError(Exception):
    """Raised when a config file is missing, malformed, or fails validation."""


class SeverityMode(enum.Enum):
    """How per-topic statuses roll up into the overall reported severity."""

    WORST = "worst"
    WEIGHTED = "weighted"
    BOTH = "both"


@dataclass(frozen=True)
class TopicConfig:
    """One declared topic: gets the full (typed, content-checked) subscription tier."""

    name: str
    msg_type: str
    weight: float = 1.0
    thresholds: dict[str, ThresholdZone] = field(default_factory=dict)
    related_topics: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ActionConfig:
    """A Nav2 action server to track goal lifecycle for."""

    name: str
    action_type: str
    weight: float = 1.0
    thresholds: dict[str, ThresholdZone] = field(default_factory=dict)


@dataclass(frozen=True)
class TFPairConfig:
    """A parent/child frame pair to watch for a valid, fresh TF chain."""

    parent: str
    child: str
    weight: float = 1.0


@dataclass(frozen=True)
class PublishConfig:
    """DiagnosticArray publish settings, decoupled from each plugin's sampling rate."""

    rate_hz: float = 5.0
    topic: str = "/diagnostics"


@dataclass(frozen=True)
class TestudoConfig:
    """The full, validated Testudo configuration."""

    topics: dict[str, list[TopicConfig]] = field(default_factory=dict)
    actions: list[ActionConfig] = field(default_factory=list)
    tf: list[TFPairConfig] = field(default_factory=list)
    severity_mode: SeverityMode = SeverityMode.WORST
    publish: PublishConfig = field(default_factory=PublishConfig)
    plugins_dir: str | None = None


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


_TOP_LEVEL_KEYS = {"topics", "actions", "tf", "severity_mode", "publish", "plugins_dir"}


def _parse_config(raw: dict[str, Any], source: str) -> TestudoConfig:
    unknown = set(raw) - _TOP_LEVEL_KEYS
    if unknown:
        raise ConfigError(f"{source}: unknown top-level key(s): {', '.join(sorted(unknown))}")

    plugins_dir = raw.get("plugins_dir")
    if plugins_dir is not None and not isinstance(plugins_dir, str):
        raise ConfigError(f"{source}: 'plugins_dir' must be a string")

    return TestudoConfig(
        topics=_parse_topics(raw.get("topics", {}), source),
        actions=_parse_actions(raw.get("actions", []), source),
        tf=_parse_tf(raw.get("tf", []), source),
        severity_mode=_parse_severity_mode(raw.get("severity_mode", "worst"), source),
        publish=_parse_publish(raw.get("publish", {}), source),
        plugins_dir=plugins_dir,
    )


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
    return result


_TOPIC_ENTRY_KEYS = {"name", "weight", "thresholds", "related_topics"}


def _parse_topic_entry(entry: Any, msg_type: str, source: str) -> TopicConfig:
    if not isinstance(entry, dict):
        raise ConfigError(f"{source}: topics.{msg_type} entries must be mappings")
    unknown = set(entry) - _TOPIC_ENTRY_KEYS
    if unknown:
        raise ConfigError(f"{source}: topics.{msg_type} entry has unknown key(s): {', '.join(sorted(unknown))}")
    name = entry.get("name")
    if not isinstance(name, str) or not name:
        raise ConfigError(f"{source}: topics.{msg_type} entry missing required string 'name'")
    weight = _parse_weight(entry.get("weight", 1.0), f"topics.{msg_type}[{name}]", source)
    thresholds = _parse_thresholds(entry.get("thresholds", {}), f"topics.{msg_type}[{name}]", source)
    related_topics = _parse_related_topics(entry.get("related_topics", {}), f"topics.{msg_type}[{name}]", source)
    return TopicConfig(
        name=name, msg_type=msg_type, weight=weight, thresholds=thresholds, related_topics=related_topics
    )


def _parse_related_topics(raw: Any, context: str, source: str) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise ConfigError(f"{source}: {context}.related_topics must be a mapping of logical name -> topic name")
    result: dict[str, str] = {}
    for logical_name, topic_name in raw.items():
        if not isinstance(logical_name, str) or not logical_name:
            raise ConfigError(f"{source}: {context}.related_topics has a non-string or empty key")
        if not isinstance(topic_name, str) or not topic_name:
            raise ConfigError(f"{source}: {context}.related_topics.{logical_name} must be a non-empty string")
        result[logical_name] = topic_name
    return result


def _parse_thresholds(raw: Any, context: str, source: str) -> dict[str, ThresholdZone]:
    if not isinstance(raw, dict):
        raise ConfigError(f"{source}: {context}.thresholds must be a mapping")
    result: dict[str, ThresholdZone] = {}
    for metric, zones in raw.items():
        if not isinstance(zones, dict):
            raise ConfigError(f"{source}: {context}.thresholds.{metric} must be a mapping with green/orange/red")
        unknown = set(zones) - {"green", "orange", "red"}
        if unknown:
            raise ConfigError(f"{source}: {context}.thresholds.{metric} has unknown key(s): {', '.join(sorted(unknown))}")
        for key, value in zones.items():
            if value is not None and not isinstance(value, (int, float)):
                raise ConfigError(f"{source}: {context}.thresholds.{metric}.{key} must be numeric")
        result[metric] = ThresholdZone(
            green=_to_float(zones.get("green")),
            orange=_to_float(zones.get("orange")),
            red=_to_float(zones.get("red")),
        )
    return result


def _to_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _parse_weight(value: Any, context: str, source: str) -> float:
    if not isinstance(value, (int, float)):
        raise ConfigError(f"{source}: {context}.weight must be numeric")
    return float(value)


_ACTION_ENTRY_KEYS = {"name", "action_type", "weight", "thresholds"}


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
        action_type = entry.get("action_type")
        if not isinstance(action_type, str) or not action_type:
            raise ConfigError(f"{source}: action '{name}' missing required string 'action_type'")
        weight = _parse_weight(entry.get("weight", 1.0), f"actions[{name}]", source)
        thresholds = _parse_thresholds(entry.get("thresholds", {}), f"actions[{name}]", source)
        result.append(ActionConfig(name=name, action_type=action_type, weight=weight, thresholds=thresholds))
    return result


def _parse_tf(raw: Any, source: str) -> list[TFPairConfig]:
    if not isinstance(raw, list):
        raise ConfigError(f"{source}: 'tf' must be a list")
    result = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ConfigError(f"{source}: tf entries must be mappings")
        parent = entry.get("parent")
        if not isinstance(parent, str) or not parent:
            raise ConfigError(f"{source}: tf entry missing required string 'parent'")
        child = entry.get("child")
        if not isinstance(child, str) or not child:
            raise ConfigError(f"{source}: tf entry '{parent}' missing required string 'child'")
        weight = _parse_weight(entry.get("weight", 1.0), f"tf[{parent}->{child}]", source)
        result.append(TFPairConfig(parent=parent, child=child, weight=weight))
    return result


def _parse_severity_mode(raw: Any, source: str) -> SeverityMode:
    if not isinstance(raw, str):
        raise ConfigError(f"{source}: 'severity_mode' must be a string")
    try:
        return SeverityMode(raw)
    except ValueError:
        valid = ", ".join(mode.value for mode in SeverityMode)
        raise ConfigError(f"{source}: 'severity_mode' must be one of [{valid}], got {raw!r}") from None


def _parse_publish(raw: Any, source: str) -> PublishConfig:
    if not isinstance(raw, dict):
        raise ConfigError(f"{source}: 'publish' must be a mapping")
    unknown = set(raw) - {"rate_hz", "topic"}
    if unknown:
        raise ConfigError(f"{source}: publish has unknown key(s): {', '.join(sorted(unknown))}")
    rate_hz = raw.get("rate_hz", 5.0)
    if not isinstance(rate_hz, (int, float)) or rate_hz <= 0:
        raise ConfigError(f"{source}: publish.rate_hz must be a positive number")
    topic = raw.get("topic", "/diagnostics")
    if not isinstance(topic, str) or not topic:
        raise ConfigError(f"{source}: publish.topic must be a non-empty string")
    return PublishConfig(rate_hz=float(rate_hz), topic=topic)
