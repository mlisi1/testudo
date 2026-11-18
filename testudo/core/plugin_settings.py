"""A plugin instance's effective settings: its defaults, with the declaring config's overrides merged over them.

The one place this merge happens, for the live tiers and replay alike:
graphic zones replace the default per metric, severity levels per key
(`LevelsOverride.over`), so `{warn: 0.05}` keeps a plugin's default `error`.
"""
from __future__ import annotations

from testudo.core.config import ActionConfig, TopicConfig
from testudo.plugins.base import CheckPlugin, SeverityLevels, ThresholdZone


def build_plugin(
    plugin_class: type[CheckPlugin], related_topics: dict[str, str], *configs: TopicConfig | ActionConfig | None
) -> CheckPlugin:
    """A `plugin_class` instance with `plugin_settings` applied.

    `levels` is assigned after construction rather than passed to
    `__init__`, so a plugin whose own `__init__` predates the keyword
    still works.
    """
    thresholds, levels = plugin_settings(plugin_class, *configs)
    plugin = plugin_class(thresholds=thresholds, related_topics=related_topics)
    plugin.levels = levels
    # An optional, duck-typed hook like TF's `set_watched_pairs`: a setting
    # one plugin reads, which the plugin contract shouldn't have to carry.
    frame_role = next((role for role in (getattr(config, "frame_role", None) for config in configs) if role), None)
    if frame_role is not None and hasattr(plugin, "set_frame_role"):
        plugin.set_frame_role(frame_role)
    return plugin


def plugin_settings(
    plugin_class: type[CheckPlugin], *configs: TopicConfig | ActionConfig | None
) -> tuple[dict[str, ThresholdZone], dict[str, SeverityLevels]]:
    """(thresholds, levels) for a `plugin_class` instance declared by `configs` (None entries skipped), in order."""
    thresholds = dict(plugin_class.default_thresholds())
    levels = dict(plugin_class.default_levels())
    for config in configs:
        if config is None:
            continue
        thresholds.update(config.thresholds)
        for metric, override in config.levels.items():
            levels[metric] = override.over(levels.get(metric))
    return thresholds, levels
