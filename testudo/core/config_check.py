"""Settings that load fine but would do nothing: a misspelled metric under `thresholds:`.

The loader can't catch these, since which metrics exist depends on the
plugins discovered. A warning rather than an error: a third-party plugin may
read a metric it declares no default for.
"""
from __future__ import annotations

import logging

from testudo.core.config import TestudoConfig
from testudo.core.stamp_check import STAMP_FUTURE_METRIC, STAMP_LAG_METRIC
from testudo.plugins.base import CheckPlugin
from testudo.plugins.registry import DiscoveredPlugin

_logger = logging.getLogger(__name__)

_ACTION_STATUS_TYPE = "action_msgs/msg/GoalStatusArray"
#: Judged by Testudo itself: the rate on any declared topic, stamps on any a plugin checks.
_RATE_METRIC = "rate_hz"
_STAMP_METRICS = frozenset({STAMP_LAG_METRIC, STAMP_FUTURE_METRIC})


def _plugin_metrics(plugin: type[CheckPlugin]) -> set[str]:
    return {*plugin.default_levels(), *plugin.default_thresholds()}


def unknown_metrics(config: TestudoConfig, plugins: list[DiscoveredPlugin]) -> list[str]:
    """One line for each metric in `config` that nothing reads, naming where it is."""
    plugin_by_type: dict[str, type[CheckPlugin]] = {}
    for discovered in plugins:
        for msg_type in discovered.plugin_class.msg_types():
            plugin_by_type.setdefault(msg_type, discovered.plugin_class)

    problems = []
    for msg_type, entries in config.topics.items():
        plugin = plugin_by_type.get(msg_type)
        known = {_RATE_METRIC} if plugin is None else {_RATE_METRIC, *_STAMP_METRICS, *_plugin_metrics(plugin)}
        for entry in entries:
            for metric in sorted({*entry.levels, *entry.thresholds} - known):
                problems.append(f"topics.{msg_type}[{entry.name}].thresholds.{metric} (valid: {', '.join(sorted(known))})")

    action_plugin = plugin_by_type.get(_ACTION_STATUS_TYPE)
    if action_plugin is not None:
        known = _plugin_metrics(action_plugin)
        for action in config.actions:
            for metric in sorted({*action.levels, *action.thresholds} - known):
                problems.append(f"actions[{action.name}].thresholds.{metric} (valid: {', '.join(sorted(known))})")
    return problems


def warn_unknown_metrics(config: TestudoConfig, plugins: list[DiscoveredPlugin], source: str) -> None:
    """Log a WARNING for each metric in `config` that has no effect."""
    for problem in unknown_metrics(config, plugins):
        _logger.warning("%s: no check reads %s", source, problem)
