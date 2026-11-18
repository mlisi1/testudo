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
    FaultRetention,
    FaultsConfig,
    FRAME_ROLE_MSG_TYPES,
    FRAME_ROLES,
    LevelsOverride,
    NodeProfilingConfig,
    PublishConfig,
    SeverityMode,
    TestudoConfig,
    TFPairConfig,
    TopicConfig,
)
from testudo.core.config_values import (
    parse_code_overrides,
    parse_exclude_rules,
    parse_non_empty_string,
    parse_positive_number,
    parse_metric,
)
from testudo.plugins.base import SeverityLevels, ThresholdZone


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
        faults=_parse_faults(raw.get("faults", {}), source, publish),
        check_undeclared_topics=check_undeclared_topics,
        max_check_rate_hz=parse_positive_number(
            raw.get("max_check_rate_hz", DEFAULT_MAX_CHECK_RATE_HZ), "'max_check_rate_hz'", source
        ),
        debounce_samples=debounce_samples,
    )


def _parse_exclude_topics(raw: Any, source: str) -> list[ExcludeRule]:
    return parse_exclude_rules(raw, source, key="exclude_topics")


def _parse_exclude_nodes(raw: Any, source: str) -> list[ExcludeRule]:
    return parse_exclude_rules(raw, source, key="exclude_nodes")


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
    weight = _parse_weight(entry.get("weight", 1.0), f"topics.{msg_type}[{name}]", source)
    thresholds, levels = _parse_thresholds(entry.get("thresholds", {}), f"topics.{msg_type}[{name}]", source)
    codes = parse_code_overrides(entry.get("codes", {}), f"topics.{msg_type}[{name}]", source)
    related_topics = _parse_related_topics(entry.get("related_topics", {}), f"topics.{msg_type}[{name}]", source)
    event_driven = entry.get("event_driven", False)
    if not isinstance(event_driven, bool):
        raise ConfigError(f"{source}: topics.{msg_type}[{name}].event_driven must be true or false")
    frame_role = entry.get("frame_role")
    if frame_role is not None and frame_role not in FRAME_ROLES:
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
        if not isinstance(topic_name, str) or not topic_name:
            raise ConfigError(f"{source}: {context}.related_topics.{logical_name} must be a non-empty string")
        result[logical_name] = topic_name
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


def _parse_weight(value: Any, context: str, source: str) -> float:
    if not isinstance(value, (int, float)):
        raise ConfigError(f"{source}: {context}.weight must be numeric")
    return float(value)


_ACTION_ENTRY_KEYS = {"name", "action_type", "weight", "thresholds", "codes", "related_topics"}


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
        thresholds, levels = _parse_thresholds(entry.get("thresholds", {}), f"actions[{name}]", source)
        codes = parse_code_overrides(entry.get("codes", {}), f"actions[{name}]", source)
        related_topics = _parse_related_topics(entry.get("related_topics", {}), f"actions[{name}]", source)
        result.append(
            ActionConfig(
                name=name,
                action_type=action_type,
                weight=weight,
                thresholds=thresholds,
                levels=levels,
                codes=codes,
                related_topics=related_topics,
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
}


def _parse_node_levels(raw: dict, metric: str, default: SeverityLevels, source: str) -> SeverityLevels:
    """A node-profiling metric's warn/error over `default`; green/orange/red are refused, since no meter uses them."""
    if metric not in raw:
        return default
    if isinstance(raw[metric], dict) and any(key in raw[metric] for key in ("green", "orange", "red")):
        raise ConfigError(
            f"{source}: node_profiling.{metric} takes warn/error (green/orange/red would color nothing here)"
        )
    _zone, levels = parse_metric(raw[metric], f"node_profiling.{metric}", source)
    return levels.over(default) if levels is not None else default


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
        poll_interval_seconds=parse_positive_number(
            raw.get("poll_interval_seconds", default.poll_interval_seconds), "node_profiling.poll_interval_seconds", source
        ),
        rediscovery_interval_seconds=parse_positive_number(
            raw.get("rediscovery_interval_seconds", default.rediscovery_interval_seconds),
            "node_profiling.rediscovery_interval_seconds",
            source,
        ),
        gpu_enabled=gpu_enabled,
        exclude_nodes=_parse_exclude_nodes(raw.get("exclude_nodes", []), source),
        cpu_percent=_parse_node_levels(raw, "cpu_percent", default.cpu_percent, source),
        memory_mb=_parse_node_levels(raw, "memory_mb", default.memory_mb, source),
        ctx_switches_per_sec=_parse_node_levels(raw, "ctx_switches_per_sec", default.ctx_switches_per_sec, source),
        memory_slope_mb_per_min=_parse_node_levels(
            raw, "memory_slope_mb_per_min", default.memory_slope_mb_per_min, source
        ),
        publish_topic=parse_non_empty_string(
            raw.get("publish_topic", default_node_profiling_topic(publish)), "node_profiling.publish_topic", source
        ),
    )


def default_faults_topic(publish: PublishConfig) -> str:
    """Where fault history publishes unless `faults.publish_topic` is set (like `default_node_profiling_topic`)."""
    return f"{publish.namespace}/faults" if publish.per_plugin else f"{publish.topic.rstrip('/')}/faults"


def _parse_faults(raw: Any, source: str, publish: PublishConfig) -> FaultsConfig:
    if not isinstance(raw, dict):
        raise ConfigError(f"{source}: 'faults' must be a mapping")
    unknown = set(raw) - {"retention", "recent_window_seconds", "publish_topic"}
    if unknown:
        raise ConfigError(f"{source}: faults has unknown key(s): {', '.join(sorted(unknown))}")
    retention = raw.get("retention", FaultRetention.SESSION.value)
    try:
        retention = FaultRetention(retention)
    except ValueError:
        valid = ", ".join(mode.value for mode in FaultRetention)
        raise ConfigError(f"{source}: faults.retention must be one of [{valid}], got {retention!r}") from None
    return FaultsConfig(
        retention=retention,
        recent_window_seconds=parse_positive_number(
            raw.get("recent_window_seconds", FaultsConfig.recent_window_seconds), "faults.recent_window_seconds", source
        ),
        publish_topic=parse_non_empty_string(
            raw.get("publish_topic", default_faults_topic(publish)), "faults.publish_topic", source
        ),
    )

