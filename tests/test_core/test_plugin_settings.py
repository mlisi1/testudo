"""plugin_settings / build_plugin: config merged over plugin defaults, no ROS graph."""
from __future__ import annotations

from typing import Any

from testudo.core.config import ActionConfig, LevelsOverride, TopicConfig
from testudo.core.plugin_settings import build_plugin, plugin_settings
from testudo.plugins.base import CheckPlugin, CheckStatus, Severity, SeverityLevels, ThresholdZone


class _Defaults(CheckPlugin):
    @classmethod
    def msg_types(cls) -> tuple[str, ...]:
        return ("std_msgs/msg/Empty",)

    @classmethod
    def default_thresholds(cls) -> dict[str, ThresholdZone]:
        return {"a": ThresholdZone(green=1.0, orange=2.0)}

    @classmethod
    def default_levels(cls) -> dict[str, SeverityLevels]:
        return {"a": SeverityLevels(warn=1.0, error=100.0), "b": SeverityLevels(warn=5.0)}

    def on_message(self, topic: str, msg: Any) -> None:
        pass

    def get_status(self) -> CheckStatus:
        return CheckStatus(Severity.OK, "x", "")


class _OldStyleInit(_Defaults):
    """A plugin written before `levels=` existed: its __init__ doesn't take it."""

    def __init__(self, thresholds=None, related_topics=None) -> None:
        super().__init__(thresholds, related_topics)


def test_build_plugin_hands_a_configured_frame_role_to_the_plugin() -> None:
    from testudo.plugins.builtin.localization import LocalizationPlugin

    config = TopicConfig(name="/pose", msg_type="nav_msgs/msg/Odometry", frame_role="global")
    plugin = build_plugin(LocalizationPlugin, {}, config)
    assert plugin.get_status() is not None
    assert plugin._role() == "global"


def test_defaults_without_config() -> None:
    thresholds, levels = plugin_settings(_Defaults, None)
    assert thresholds == {"a": ThresholdZone(green=1.0, orange=2.0)}
    assert levels == {"a": SeverityLevels(warn=1.0, error=100.0), "b": SeverityLevels(warn=5.0)}


def test_topic_then_action_config_merge_over_defaults_key_by_key() -> None:
    topic = TopicConfig(
        name="/t",
        msg_type="std_msgs/msg/Empty",
        thresholds={"a": ThresholdZone(green=3.0)},
        levels={"a": LevelsOverride(warn=0.5, given=frozenset({"warn"}))},
    )
    action = ActionConfig(
        name="/x",
        levels={"c": LevelsOverride(error=7.0, given=frozenset({"error"}))},
    )
    thresholds, levels = plugin_settings(_Defaults, topic, action)
    assert thresholds["a"] == ThresholdZone(green=3.0)
    assert levels["a"] == SeverityLevels(warn=0.5, error=100.0)
    assert levels["c"] == SeverityLevels(error=7.0)


def test_build_plugin_works_with_an_init_that_predates_levels_and_reset_keeps_them() -> None:
    topic = TopicConfig(
        name="/t", msg_type="std_msgs/msg/Empty", levels={"b": LevelsOverride(warn=9.0, given=frozenset({"warn"}))}
    )
    plugin = build_plugin(_OldStyleInit, {}, topic)
    assert plugin.levels["b"] == SeverityLevels(warn=9.0)
    plugin.reset()
    assert plugin.levels["b"] == SeverityLevels(warn=9.0)
