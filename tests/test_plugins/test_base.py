"""Unit tests for the CheckPlugin contract using a synthetic plugin."""
from __future__ import annotations

import pytest
from diagnostic_msgs.msg import DiagnosticStatus

from testudo.plugins.base import CheckPlugin, CheckStatus, Severity, ThresholdZone, evaluate_zone


def test_check_plugin_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        CheckPlugin()  # type: ignore[abstract]


class _SyntheticPlugin(CheckPlugin):
    """Minimal concrete plugin used only to exercise the base contract."""

    def __init__(self, thresholds=None, related_topics=None) -> None:
        super().__init__(thresholds, related_topics)
        self.last_msg = None
        self.ticks = 0

    @classmethod
    def msg_types(cls) -> tuple[str, ...]:
        return ("std_msgs/msg/String",)

    @classmethod
    def default_thresholds(cls) -> dict[str, ThresholdZone]:
        return {"foo": ThresholdZone(green=1.0)}

    def on_message(self, topic: str, msg: object) -> None:
        self.last_msg = msg

    def on_tick(self, now_seconds: float) -> None:
        self.ticks += 1

    def get_status(self) -> CheckStatus:
        return CheckStatus(severity=Severity.OK, label="synthetic", message="fine")


def test_concrete_subclass_implements_full_lifecycle() -> None:
    plugin = _SyntheticPlugin()
    assert plugin.msg_types() == ("std_msgs/msg/String",)
    assert plugin.default_thresholds() == {"foo": ThresholdZone(green=1.0)}

    plugin.on_message("/topic", "hello")
    assert plugin.last_msg == "hello"

    plugin.on_tick(1.0)
    assert plugin.ticks == 1

    status = plugin.get_status()
    assert status.severity == Severity.OK
    assert status.label == "synthetic"


def test_init_defaults_thresholds_to_default_thresholds() -> None:
    plugin = _SyntheticPlugin()
    assert plugin.thresholds == {"foo": ThresholdZone(green=1.0)}
    assert plugin.related_topics == {}


def test_init_accepts_effective_thresholds_and_related_topics() -> None:
    overridden = {"foo": ThresholdZone(green=5.0)}
    plugin = _SyntheticPlugin(thresholds=overridden, related_topics={"cmd_vel": "/cmd_vel"})
    assert plugin.thresholds == overridden
    assert plugin.related_topics == {"cmd_vel": "/cmd_vel"}


def test_evaluate_zone_none_zone_is_always_ok() -> None:
    assert evaluate_zone(1000.0, None) == Severity.OK


def test_evaluate_zone_higher_is_worse() -> None:
    zone = ThresholdZone(green=1.0, orange=2.0)
    assert evaluate_zone(0.5, zone) == Severity.OK
    assert evaluate_zone(1.0, zone) == Severity.OK
    assert evaluate_zone(1.5, zone) == Severity.WARN
    assert evaluate_zone(2.0, zone) == Severity.WARN
    assert evaluate_zone(2.5, zone) == Severity.ERROR


def test_evaluate_zone_lower_is_worse() -> None:
    zone = ThresholdZone(green=10.0, orange=5.0)
    assert evaluate_zone(20.0, zone, higher_is_worse=False) == Severity.OK
    assert evaluate_zone(7.0, zone, higher_is_worse=False) == Severity.WARN
    assert evaluate_zone(1.0, zone, higher_is_worse=False) == Severity.ERROR


def test_evaluate_zone_missing_boundaries_are_unconstrained() -> None:
    assert evaluate_zone(1000.0, ThresholdZone()) == Severity.ERROR
    assert evaluate_zone(1000.0, ThresholdZone(green=1.0)) == Severity.ERROR
    assert evaluate_zone(0.5, ThresholdZone(green=1.0)) == Severity.OK


def test_evaluate_zone_ignores_red() -> None:
    zone = ThresholdZone(green=1.0, orange=2.0, red=3.0)
    assert evaluate_zone(2.5, zone) == Severity.ERROR
    assert evaluate_zone(100.0, zone) == Severity.ERROR


def test_severity_values_align_with_diagnostic_status() -> None:
    # DiagnosticStatus's constants are `bytes` (IDL type `byte`); Severity
    # converts them to plain int, so compare via the same conversion.
    assert Severity.OK == int.from_bytes(DiagnosticStatus.OK, "little")
    assert Severity.WARN == int.from_bytes(DiagnosticStatus.WARN, "little")
    assert Severity.ERROR == int.from_bytes(DiagnosticStatus.ERROR, "little")
    assert Severity.STALE == int.from_bytes(DiagnosticStatus.STALE, "little")
    assert Severity.OK < Severity.WARN < Severity.ERROR < Severity.STALE


def test_threshold_zone_defaults_to_none() -> None:
    zone = ThresholdZone()
    assert zone.green is None
    assert zone.orange is None
    assert zone.red is None
