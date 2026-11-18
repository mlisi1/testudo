"""Changes a running session's checks on request: code severities and `warn` levels, from the TUI.

A companion module like exclusion.py: it updates the manager's per-topic
state so a change shows on the next report, without resubscribing. Called
from the TUI's thread while the spin thread reads the same state, so the
config indexes are replaced whole, never mutated.
"""
from __future__ import annotations

import math
from dataclasses import replace
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import TYPE_CHECKING, Callable

from testudo.core.config import ActionConfig, LevelsOverride, TopicConfig
from testudo.core.config_values import CODE_SEVERITY_NAMES
from testudo.core.stamp_check import STAMP_FUTURE_METRIC, STAMP_LAG_METRIC
from testudo.core.topic_report import TopicReport
from testudo.plugins.base import CodeMetric, Severity, SeverityLevels, evaluate_levels

if TYPE_CHECKING:
    from testudo.core.subscription_manager import SubscriptionManager

#: LIVE-004's metric: read from the topic's config on every report.
_RATE_METRIC = "rate_hz"

#: Significant digits of a level set from a measured value.
_CUT_DIGITS = 3


def owner_topic(report: TopicReport) -> str:
    """The topic whose config applies to `report` (a derived row's source)."""
    return report.source_topic or report.topic


def msg_type_of(manager: "SubscriptionManager", report: TopicReport) -> str:
    """The message type of `report`'s owning topic."""
    return manager._msg_type_by_topic.get(owner_topic(report), report.msg_type)


def severity_name(severity: int) -> str:
    """How `severity` is written under `codes:` in the config."""
    return next(name for name, value in CODE_SEVERITY_NAMES.items() if value == severity)


def code_overrides(manager: "SubscriptionManager", topic: str) -> dict[str, int]:
    """The `codes:` overrides in effect on `topic`."""
    overrides: dict[str, int] = {}
    for config in (manager._topic_config_by_name.get(topic), manager._action_config_by_status_topic.get(topic)):
        if config is not None:
            overrides.update(config.codes)
    return overrides


def _update_config(
    manager: "SubscriptionManager", topic: str, change: Callable[[TopicConfig | ActionConfig], TopicConfig | ActionConfig]
) -> None:
    """Replace `topic`'s config with `change(config)`, declaring the topic if it isn't yet."""
    action = manager._action_config_by_status_topic.get(topic)
    if action is not None:
        manager._action_config_by_status_topic = {**manager._action_config_by_status_topic, topic: change(action)}
        return
    config = manager._topic_config_by_name.get(topic)
    if config is None:
        config = TopicConfig(name=topic, msg_type=manager._msg_type_by_topic.get(topic, ""))
    manager._topic_config_by_name = {**manager._topic_config_by_name, topic: change(config)}


def set_code_override(manager: "SubscriptionManager", topic: str, code: str, severity: int | None) -> None:
    """Report `code` on `topic` at `severity` from now on; None gives it back its own severity."""

    def change(config: TopicConfig | ActionConfig) -> TopicConfig | ActionConfig:
        codes = {key: value for key, value in config.codes.items() if key != code}
        return replace(config, codes=codes if severity is None else {**codes, code: severity})

    _update_config(manager, topic, change)


def _levels_of(manager: "SubscriptionManager", topic: str, metric: str) -> SeverityLevels | None:
    """The levels `metric` is judged by on `topic` right now."""
    state = manager._full_tier.get(topic)
    if metric == _RATE_METRIC:
        config = manager._topic_config_by_name.get(topic)
        override = config.levels.get(metric) if config is not None else None
        return override.over(None) if override is not None else None
    if state is None:
        return None
    if metric == STAMP_LAG_METRIC:
        return state.stamps.lag_levels
    if metric == STAMP_FUTURE_METRIC:
        return state.stamps.future_levels
    return state.plugin.levels.get(metric)


def _follows_nav2_rate(manager: "SubscriptionManager", topic: str) -> bool:
    config = manager._topic_config_by_name.get(topic)
    return topic in manager._nav2_rates.expected and (config is None or _RATE_METRIC not in config.levels)


def warn_cut(manager: "SubscriptionManager", topic: str, metric: CodeMetric) -> float | None:
    """The `warn` level at `metric`'s current value, rounded so that value stays on the good side.

    None when `warn` can't quiet it: the value is past the metric's `error`
    level, which only the user should move, or isn't a finite number. Also
    None for a rate judged against Nav2's parameters: a `rate_hz` in the config
    replaces that check, and isn't applied to a topic that may go quiet.
    """
    if not math.isfinite(metric.value):
        return None
    if metric.name == _RATE_METRIC and _follows_nav2_rate(manager, topic):
        return None
    error = (_levels_of(manager, topic, metric.name) or SeverityLevels()).error
    if evaluate_levels(metric.value, SeverityLevels(error=error), higher_is_worse=metric.higher_is_worse) != Severity.OK:
        return None
    cut = _rounded_outward(metric.value, metric.higher_is_worse)
    if error is None:
        return cut
    return min(cut, error) if metric.higher_is_worse else max(cut, error)


def _rounded_outward(value: float, higher_is_worse: bool) -> float:
    """`value` to `_CUT_DIGITS` significant digits, rounded towards the worse side."""
    if value == 0:
        return 0.0
    # Decimal, not float division: 0.7 / 0.001 lands just under 700 and would floor to 0.699.
    exact = Decimal(repr(float(value)))
    step = Decimal(1).scaleb(exact.adjusted() - _CUT_DIGITS + 1)
    return float(exact.quantize(step, rounding=ROUND_CEILING if higher_is_worse else ROUND_FLOOR))


def set_warn_level(manager: "SubscriptionManager", topic: str, metric: str, warn: float) -> None:
    """Judge `metric` on `topic` with this `warn` from now on, leaving its `error` as it is."""

    def change(config: TopicConfig | ActionConfig) -> TopicConfig | ActionConfig:
        written = config.levels.get(metric, LevelsOverride())
        override = LevelsOverride(warn=warn, error=written.error, given=written.given | {"warn"})
        return replace(config, levels={**config.levels, metric: override})

    _update_config(manager, topic, change)
    state = manager._full_tier.get(topic)
    if state is None or metric == _RATE_METRIC:
        return
    if metric == STAMP_LAG_METRIC:
        state.stamps.lag_levels = replace(state.stamps.lag_levels, warn=warn)
    elif metric == STAMP_FUTURE_METRIC:
        state.stamps.future_levels = replace(state.stamps.future_levels, warn=warn)
    else:
        levels = state.plugin.levels.get(metric) or SeverityLevels()
        state.plugin.levels = {**state.plugin.levels, metric: replace(levels, warn=warn)}
