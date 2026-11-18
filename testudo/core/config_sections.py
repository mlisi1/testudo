"""The config's self-contained sections: `publish:`, `node_profiling:` and `faults:`.

`config_loader` handles the file and the robot's own topology (topics,
actions, TF); each section here only depends on the value parsers.
"""
from __future__ import annotations

from typing import Any

from testudo.core.config import ConfigError, FaultRetention, FaultsConfig, NodeProfilingConfig, PublishConfig
from testudo.core.config_values import parse_exclude_rules, parse_metric, parse_positive_number, parse_topic_name
from testudo.plugins.base import SeverityLevels


def parse_publish(raw: Any, source: str) -> PublishConfig:
    if not isinstance(raw, dict):
        raise ConfigError(f"{source}: 'publish' must be a mapping")
    unknown = set(raw) - {"rate_hz", "topic", "per_plugin", "namespace"}
    if unknown:
        raise ConfigError(f"{source}: publish has unknown key(s): {', '.join(sorted(unknown))}")
    rate_hz = parse_positive_number(raw.get("rate_hz", 5.0), "publish.rate_hz", source)
    topic = parse_topic_name(raw.get("topic", "/diagnostics"), "publish.topic", source)
    per_plugin = raw.get("per_plugin", True)
    if not isinstance(per_plugin, bool):
        raise ConfigError(f"{source}: publish.per_plugin must be true or false")
    namespace = raw.get("namespace", "/testudo")
    if not isinstance(namespace, str) or not namespace.startswith("/") or namespace.rstrip("/") == "":
        raise ConfigError(f"{source}: publish.namespace must be an absolute topic namespace like /testudo")
    return PublishConfig(rate_hz=rate_hz, topic=topic, per_plugin=per_plugin, namespace=namespace.rstrip("/"))


_NODE_PROFILING_KEYS = {
    "enabled",
    "poll_interval_seconds",
    "rediscovery_interval_seconds",
    "gpu_enabled",
    "exclude_nodes",
    "thresholds",
    "publish_topic",
}
_NODE_METRICS = ("cpu_percent", "memory_mb", "ctx_switches_per_s", "memory_slope_mb_per_min")


def _parse_node_levels(raw: dict, metric: str, default: SeverityLevels, source: str) -> SeverityLevels:
    """A node-profiling metric's warn/error over `default`; green/orange/red are refused, since no meter uses them."""
    if metric not in raw:
        return default
    context = f"node_profiling.thresholds.{metric}"
    if isinstance(raw[metric], dict) and any(key in raw[metric] for key in ("green", "orange", "red")):
        raise ConfigError(f"{source}: {context} takes warn/error (green/orange/red would color nothing here)")
    _zone, levels = parse_metric(raw[metric], context, source)
    return levels.over(default) if levels is not None else default


def _parse_node_thresholds(raw: Any, source: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ConfigError(f"{source}: node_profiling.thresholds must be a mapping")
    unknown = set(raw) - set(_NODE_METRICS)
    if unknown:
        raise ConfigError(
            f"{source}: node_profiling.thresholds has unknown metric(s): {', '.join(sorted(map(str, unknown)))} "
            f"(valid: {', '.join(_NODE_METRICS)})"
        )
    return raw


def default_node_profiling_topic(publish: PublishConfig) -> str:
    """Where node profiling publishes unless `publish_topic` is set: next to the rest of the output.

    `<namespace>/nodes` in per-plugin mode, `<publish.topic>/nodes` (e.g.
    `/diagnostics/nodes`) when everything goes on one combined topic.
    """
    return f"{publish.namespace}/nodes" if publish.per_plugin else f"{publish.topic.rstrip('/')}/nodes"


def parse_node_profiling(raw: Any, source: str, publish: PublishConfig) -> NodeProfilingConfig:
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
    thresholds = _parse_node_thresholds(raw.get("thresholds", {}), source)
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
        exclude_nodes=parse_exclude_rules(raw.get("exclude_nodes", []), source, key="exclude_nodes"),
        **{metric: _parse_node_levels(thresholds, metric, getattr(default, metric), source) for metric in _NODE_METRICS},
        publish_topic=parse_topic_name(
            raw.get("publish_topic", default_node_profiling_topic(publish)), "node_profiling.publish_topic", source
        ),
    )


def default_faults_topic(publish: PublishConfig) -> str:
    """Where fault history publishes unless `faults.publish_topic` is set (like `default_node_profiling_topic`)."""
    return f"{publish.namespace}/faults" if publish.per_plugin else f"{publish.topic.rstrip('/')}/faults"


def parse_faults(raw: Any, source: str, publish: PublishConfig) -> FaultsConfig:
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
        publish_topic=parse_topic_name(
            raw.get("publish_topic", default_faults_topic(publish)), "faults.publish_topic", source
        ),
    )
