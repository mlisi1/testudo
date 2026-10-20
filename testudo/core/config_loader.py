"""YAML loader/validator for the typed config schema in `config`.

Every section is parsed and validated here, once, so a malformed config
fails loudly and specifically at load time.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from testudo.core.config import (
    ActionConfig,
    ConfigError,
    ExcludeMatchType,
    ExcludeRule,
    InvalidExcludePatternError,
    NodeProfilingConfig,
    PublishConfig,
    SeverityMode,
    TestudoConfig,
    TFPairConfig,
    TopicConfig,
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
}


def _parse_config(raw: dict[str, Any], source: str) -> TestudoConfig:
    unknown = set(raw) - _TOP_LEVEL_KEYS
    if unknown:
        raise ConfigError(f"{source}: unknown top-level key(s): {', '.join(sorted(unknown))}")

    plugins_dir = raw.get("plugins_dir")
    if plugins_dir is not None and not isinstance(plugins_dir, str):
        raise ConfigError(f"{source}: 'plugins_dir' must be a string")

    publish = _parse_publish(raw.get("publish", {}), source)
    return TestudoConfig(
        topics=_parse_topics(raw.get("topics", {}), source),
        actions=_parse_actions(raw.get("actions", []), source),
        tf=_parse_tf(raw.get("tf", []), source),
        severity_mode=_parse_severity_mode(raw.get("severity_mode", "worst"), source),
        publish=publish,
        plugins_dir=plugins_dir,
        exclude_topics=_parse_exclude_topics(raw.get("exclude_topics", []), source),
        node_profiling=_parse_node_profiling(raw.get("node_profiling", {}), source, publish),
    )


_EXCLUDE_RULE_KEYS = {"pattern", "type"}


def _parse_exclude_topics(raw: Any, source: str) -> list[ExcludeRule]:
    return _parse_exclude_rules(raw, source, key="exclude_topics")


def _parse_exclude_nodes(raw: Any, source: str) -> list[ExcludeRule]:
    return _parse_exclude_rules(raw, source, key="exclude_nodes")


def _parse_exclude_rules(raw: Any, source: str, key: str) -> list[ExcludeRule]:
    if not isinstance(raw, list):
        raise ConfigError(f"{source}: '{key}' must be a list of patterns")
    return [_parse_exclude_rule(entry, source, key) for entry in raw]


def _parse_exclude_rule(entry: Any, source: str, key: str = "exclude_topics") -> ExcludeRule:
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
        match_type = _parse_exclude_match_type(entry.get("type", "literal"), source, key)
    else:
        raise ConfigError(f"{source}: {key} entries must be a string or a {{pattern, type}} mapping")
    if not isinstance(pattern, str) or not pattern:
        raise ConfigError(f"{source}: {key} entries must have a non-empty string 'pattern'")
    try:
        return ExcludeRule(pattern=pattern, match_type=match_type)
    except InvalidExcludePatternError as exc:
        raise ConfigError(f"{source}: {key}: {exc}") from exc


def _parse_exclude_match_type(raw: Any, source: str, key: str = "exclude_topics") -> ExcludeMatchType:
    if not isinstance(raw, str):
        raise ConfigError(f"{source}: {key} entry 'type' must be a string")
    try:
        return ExcludeMatchType(raw)
    except ValueError:
        valid = ", ".join(t.value for t in ExcludeMatchType)
        raise ConfigError(f"{source}: {key} entry 'type' must be one of [{valid}], got {raw!r}") from None


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


_TOPIC_ENTRY_KEYS = {"name", "weight", "thresholds", "related_topics", "event_driven"}


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
    event_driven = entry.get("event_driven", False)
    if not isinstance(event_driven, bool):
        raise ConfigError(f"{source}: topics.{msg_type}[{name}].event_driven must be true or false")
    return TopicConfig(
        name=name,
        msg_type=msg_type,
        weight=weight,
        thresholds=thresholds,
        related_topics=related_topics,
        event_driven=event_driven,
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
    return {metric: _parse_zone(zones, f"{context}.thresholds.{metric}", source) for metric, zones in raw.items()}


def _parse_zone(raw: Any, context: str, source: str) -> ThresholdZone:
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


def _parse_weight(value: Any, context: str, source: str) -> float:
    if not isinstance(value, (int, float)):
        raise ConfigError(f"{source}: {context}.weight must be numeric")
    return float(value)


_ACTION_ENTRY_KEYS = {"name", "action_type", "weight", "thresholds", "related_topics"}


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
        related_topics = _parse_related_topics(entry.get("related_topics", {}), f"actions[{name}]", source)
        result.append(
            ActionConfig(
                name=name, action_type=action_type, weight=weight, thresholds=thresholds, related_topics=related_topics
            )
        )
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
    unknown = set(raw) - {"rate_hz", "topic", "per_plugin", "namespace"}
    if unknown:
        raise ConfigError(f"{source}: publish has unknown key(s): {', '.join(sorted(unknown))}")
    rate_hz = raw.get("rate_hz", 5.0)
    if not isinstance(rate_hz, (int, float)) or rate_hz <= 0:
        raise ConfigError(f"{source}: publish.rate_hz must be a positive number")
    topic = raw.get("topic", "/diagnostics")
    if not isinstance(topic, str) or not topic:
        raise ConfigError(f"{source}: publish.topic must be a non-empty string")
    per_plugin = raw.get("per_plugin", True)
    if not isinstance(per_plugin, bool):
        raise ConfigError(f"{source}: publish.per_plugin must be true or false")
    namespace = raw.get("namespace", "/testudo")
    if not isinstance(namespace, str) or not namespace.startswith("/") or namespace.rstrip("/") == "":
        raise ConfigError(f"{source}: publish.namespace must be an absolute topic namespace like /testudo")
    return PublishConfig(rate_hz=float(rate_hz), topic=topic, per_plugin=per_plugin, namespace=namespace.rstrip("/"))


_NODE_PROFILING_KEYS = {
    "enabled",
    "poll_interval_seconds",
    "rediscovery_interval_seconds",
    "gpu_enabled",
    "exclude_nodes",
    "cpu_percent",
    "memory_mb",
    "ctx_switches_per_sec",
    "memory_slope_mb_per_min",
    "publish_topic",
    "publish_rate_hz",
}


def default_node_profiling_topic(publish: PublishConfig) -> str:
    """Where node profiling publishes unless `publish_topic` is set: next to the rest of the output.

    `<namespace>/nodes` in per-plugin mode, `<publish.topic>/nodes` (e.g.
    `/diagnostics/nodes`) when everything goes on one combined topic.
    """
    return f"{publish.namespace}/nodes" if publish.per_plugin else f"{publish.topic.rstrip('/')}/nodes"


def _parse_node_profiling(raw: Any, source: str, publish: PublishConfig) -> NodeProfilingConfig:
    if not isinstance(raw, dict):
        raise ConfigError(f"{source}: 'node_profiling' must be a mapping")
    unknown = set(raw) - _NODE_PROFILING_KEYS
    if unknown:
        raise ConfigError(f"{source}: node_profiling has unknown key(s): {', '.join(sorted(unknown))}")

    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ConfigError(f"{source}: node_profiling.enabled must be a boolean")
    gpu_enabled = raw.get("gpu_enabled", True)
    if not isinstance(gpu_enabled, bool):
        raise ConfigError(f"{source}: node_profiling.gpu_enabled must be a boolean")

    default = NodeProfilingConfig()
    return NodeProfilingConfig(
        enabled=enabled,
        poll_interval_seconds=_parse_positive_number(
            raw.get("poll_interval_seconds", default.poll_interval_seconds), "node_profiling.poll_interval_seconds", source
        ),
        rediscovery_interval_seconds=_parse_positive_number(
            raw.get("rediscovery_interval_seconds", default.rediscovery_interval_seconds),
            "node_profiling.rediscovery_interval_seconds",
            source,
        ),
        gpu_enabled=gpu_enabled,
        exclude_nodes=_parse_exclude_nodes(raw.get("exclude_nodes", []), source),
        cpu_percent=_parse_zone(raw["cpu_percent"], "node_profiling.cpu_percent", source)
        if "cpu_percent" in raw
        else default.cpu_percent,
        memory_mb=_parse_zone(raw["memory_mb"], "node_profiling.memory_mb", source)
        if "memory_mb" in raw
        else default.memory_mb,
        ctx_switches_per_sec=_parse_zone(raw["ctx_switches_per_sec"], "node_profiling.ctx_switches_per_sec", source)
        if "ctx_switches_per_sec" in raw
        else default.ctx_switches_per_sec,
        memory_slope_mb_per_min=_parse_zone(
            raw["memory_slope_mb_per_min"], "node_profiling.memory_slope_mb_per_min", source
        )
        if "memory_slope_mb_per_min" in raw
        else default.memory_slope_mb_per_min,
        publish_topic=_parse_non_empty_string(
            raw.get("publish_topic", default_node_profiling_topic(publish)), "node_profiling.publish_topic", source
        ),
        publish_rate_hz=_parse_positive_number(
            raw.get("publish_rate_hz", default.publish_rate_hz), "node_profiling.publish_rate_hz", source
        ),
    )


def _parse_positive_number(value: Any, context: str, source: str) -> float:
    if not isinstance(value, (int, float)) or value <= 0:
        raise ConfigError(f"{source}: {context} must be a positive number")
    return float(value)


def _parse_non_empty_string(value: Any, context: str, source: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{source}: {context} must be a non-empty string")
    return value
