"""Unit tests for diagnostic conversion and publish-timer wiring."""
from __future__ import annotations

from dataclasses import dataclass

from testudo.core.config import PublishConfig, SeverityMode
from testudo.core.publisher import (
    DiagnosticOutput,
    DiagnosticPublisher,
    build_diagnostic_array,
    to_diagnostic_status,
)
from testudo.core.topic_report import TopicReport
from testudo.plugins.base import CheckStatus, Severity


def test_to_diagnostic_status_maps_fields() -> None:
    status = CheckStatus(severity=Severity.WARN, label="liveness", message="stale", values={"age_s": "5.0"})
    diag = to_diagnostic_status("/odom", status)
    assert diag.name == "testudo: /odom"
    assert diag.level == bytes([Severity.WARN])
    assert diag.message == "stale"
    assert diag.hardware_id == "testudo"
    assert [(kv.key, kv.value) for kv in diag.values] == [("age_s", "5.0")]


def test_to_diagnostic_status_level_is_single_byte() -> None:
    status = CheckStatus(severity=Severity.STALE, label="x", message="m")
    diag = to_diagnostic_status("/x", status)
    assert diag.level == b"\x03"
    assert len(diag.level) == 1


def test_build_diagnostic_array_includes_overall_entry() -> None:
    reports = [
        TopicReport("/a", "t", "vitals", CheckStatus(severity=Severity.OK, label="x", message="ok")),
        TopicReport("/b", "t", "full", CheckStatus(severity=Severity.ERROR, label="x", message="bad")),
    ]
    overall = CheckStatus(severity=Severity.ERROR, label="overall", message="2 topic(s)")
    array = build_diagnostic_array(stamp="fake-stamp", reports=reports, overall=overall)
    assert array.header.stamp == "fake-stamp"
    names = [s.name for s in array.status]
    assert names == ["testudo: /a", "testudo: /b", "testudo: overall"]


# --- DiagnosticPublisher wiring (fake node, no live ROS) --------------------


@dataclass
class _FakeClockNow:
    def to_msg(self):
        return "fake-stamp"


class _FakeClock:
    def now(self):
        return _FakeClockNow()


class _FakePublisher:
    def __init__(self):
        self.published = []

    def publish(self, msg):
        self.published.append(msg)


class _FakeTimer:
    def __init__(self, period, callback):
        self.period = period
        self.callback = callback
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class _FakeNode:
    def __init__(self):
        self.publishers: dict[str, _FakePublisher] = {}
        self.timer: _FakeTimer | None = None
        self.destroyed_timers = []

    def create_publisher(self, msg_type, topic, depth):
        return self.publishers.setdefault(topic, _FakePublisher())

    def create_timer(self, period, callback):
        self.timer = _FakeTimer(period, callback)
        return self.timer

    def destroy_timer(self, timer):
        self.destroyed_timers.append(timer)

    def get_clock(self):
        return _FakeClock()


class _FakeManager:
    def __init__(self, reports, overall):
        self._reports = reports
        self._overall = overall
        self.tick_calls = 0

    def tick(self):
        self.tick_calls += 1

    def reports(self):
        return self._reports

    def overall_status(self, mode):
        return self._overall


def test_diagnostic_publisher_creates_timer_at_configured_period() -> None:
    node = _FakeNode()
    manager = _FakeManager([], CheckStatus(severity=Severity.OK, label="overall", message="ok"))
    DiagnosticPublisher(node, manager, SeverityMode.WORST, PublishConfig(rate_hz=5.0), [])
    assert node.timer is not None
    assert node.timer.period == 0.2


def test_diagnostic_publisher_tick_ticks_manager_and_publishes() -> None:
    node = _FakeNode()
    report = TopicReport("/a", "t", "vitals", CheckStatus(severity=Severity.OK, label="x", message="ok"))
    overall = CheckStatus(severity=Severity.OK, label="overall", message="1 topic(s)")
    manager = _FakeManager([report], overall)
    publish_config = PublishConfig(rate_hz=5.0, per_plugin=False)
    publisher = DiagnosticPublisher(node, manager, SeverityMode.WORST, publish_config, [])

    node.timer.callback()  # simulate a timer fire

    assert manager.tick_calls == 1
    assert list(node.publishers) == ["/diagnostics"]
    assert len(node.publishers["/diagnostics"].published) == 1
    published = node.publishers["/diagnostics"].published[0]
    assert [s.name for s in published.status] == ["testudo: /a", "testudo: overall"]

    publisher.destroy()
    assert node.timer.cancelled is True
    assert node.timer in node.destroyed_timers


def _plugins():
    from testudo.plugins.builtin.odometry import OdometryPlugin
    from testudo.plugins.builtin.image_stream import ImageStreamPlugin
    from testudo.plugins.registry import DiscoveredPlugin

    return [
        DiscoveredPlugin("odometry", OdometryPlugin, "packaged"),
        DiscoveredPlugin("image_stream", ImageStreamPlugin, "packaged"),
    ]


def _ok(topic: str, msg_type: str, tier: str) -> TopicReport:
    return TopicReport(topic, msg_type, tier, CheckStatus(severity=Severity.OK, label="x", message="ok"))


def test_per_plugin_output_splits_reports_by_plugin_with_vitals_and_overall_topics() -> None:
    node = _FakeNode()
    output = DiagnosticOutput(node, PublishConfig(), _plugins())
    reports = [
        _ok("/odom", "nav_msgs/msg/Odometry", "full"),
        _ok("/camera/image_raw", "sensor_msgs/msg/Image", "presence"),  # not subscribed, still the plugin's topic
        _ok("/temp", "sensor_msgs/msg/Temperature", "vitals"),
        _ok("/odom_undeclared_vitals", "nav_msgs/msg/Odometry", "vitals"),  # no plugin checks it
    ]
    output.publish("stamp", reports, CheckStatus(severity=Severity.OK, label="overall", message="ok"))

    def names(topic: str) -> list[str]:
        return [status.name for status in node.publishers[topic].published[-1].status]

    assert set(node.publishers) == {"/testudo/odometry", "/testudo/image_stream", "/testudo/vitals", "/testudo/overall"}
    assert names("/testudo/odometry") == ["testudo: /odom"]
    assert names("/testudo/image_stream") == ["testudo: /camera/image_raw"]
    assert names("/testudo/vitals") == ["testudo: /temp", "testudo: /odom_undeclared_vitals"]
    assert names("/testudo/overall") == ["testudo: overall"]


def test_per_plugin_output_keeps_publishing_an_empty_array_once_a_plugins_topics_are_gone() -> None:
    node = _FakeNode()
    output = DiagnosticOutput(node, PublishConfig(namespace="/robot"), _plugins())
    overall = CheckStatus(severity=Severity.OK, label="overall", message="ok")
    output.publish("stamp", [_ok("/odom", "nav_msgs/msg/Odometry", "full")], overall)
    output.publish("stamp", [], overall)
    assert node.publishers["/robot/odometry"].published[-1].status == []
