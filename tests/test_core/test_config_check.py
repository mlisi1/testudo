"""Unit tests for metrics that load fine but that no check reads."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from testudo.core import config_check
from testudo.core.config import ActionConfig, LevelsOverride, TestudoConfig, TopicConfig
from testudo.core.config_check import unknown_metrics, warn_unknown_metrics
from testudo.core.config_loader import load_config
from testudo.plugins import builtin
from testudo.plugins.registry import discover_all_plugins

_LEVEL = LevelsOverride(warn=1.0, given=frozenset({"warn"}))
_IMU = "sensor_msgs/msg/Imu"
_PLUGINS = discover_all_plugins(None)


def _topic(msg_type: str, *metrics: str) -> TestudoConfig:
    levels = {metric: _LEVEL for metric in metrics}
    return TestudoConfig(topics={msg_type: [TopicConfig(name="/x", msg_type=msg_type, levels=levels)]})


def test_a_misspelled_metric_is_named_with_the_valid_ones() -> None:
    problems = unknown_metrics(_topic(_IMU, "stuck_cont"), _PLUGINS)
    assert len(problems) == 1
    assert "[/x].thresholds.stuck_cont" in problems[0] and "stuck_count" in problems[0]


def test_a_plugin_metric_and_the_generic_ones_are_known() -> None:
    assert not unknown_metrics(_topic(_IMU, "stuck_count", "rate_hz", "stamp_lag_s", "stamp_future_s"), _PLUGINS)


def test_a_topic_no_plugin_checks_only_has_a_rate() -> None:
    battery = "sensor_msgs/msg/BatteryState"
    assert not unknown_metrics(_topic(battery, "rate_hz"), _PLUGINS)
    assert unknown_metrics(_topic(battery, "stamp_lag_s"), _PLUGINS)


def test_an_action_metric_is_checked_too() -> None:
    good = ActionConfig(name="spin", levels={"frequency_per_min": _LEVEL, "avg_duration_s": _LEVEL})
    bad = ActionConfig(name="dock", levels={"frequency": _LEVEL})
    problems = unknown_metrics(TestudoConfig(actions=[good, bad]), _PLUGINS)
    assert len(problems) == 1 and "actions[dock].thresholds.frequency" in problems[0]


def test_the_warning_names_the_file(monkeypatch: pytest.MonkeyPatch) -> None:
    warnings: list[str] = []
    monkeypatch.setattr(config_check._logger, "warning", lambda text, *args: warnings.append(text % args))
    warn_unknown_metrics(_topic(_IMU, "stuck_cont"), _PLUGINS, "robot.yaml")
    assert len(warnings) == 1 and warnings[0].startswith("robot.yaml: no check reads")


def test_the_example_config_has_no_unknown_metric() -> None:
    example = Path(__file__).parents[2] / "config" / "example_config.yaml"
    assert unknown_metrics(load_config(example), _PLUGINS) == []


def test_every_metric_a_builtin_plugin_reads_has_a_default() -> None:
    known = {metric for item in _PLUGINS for metric in (*item.plugin_class.default_levels(), *item.plugin_class.default_thresholds())}
    sources = Path(builtin.__file__).parent.glob("*.py")
    read = {metric for path in sources for metric in re.findall(r'levels\.get\("(\w+)"\)', path.read_text())}
    assert read and read <= known
