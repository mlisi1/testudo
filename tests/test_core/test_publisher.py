"""Unit tests for diagnostic conversion and DiagnosticOutput's topic routing."""
from __future__ import annotations

from testudo.core.config import PublishConfig
from testudo.core.publisher import (
    DiagnosticOutput,
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


# --- DiagnosticOutput wiring (fake node, no live ROS) -----------------------


class _FakePublisher:
    def __init__(self):
        self.published = []

    def publish(self, msg):
        self.published.append(msg)


class _FakeNode:
    def __init__(self):
        self.publishers: dict[str, _FakePublisher] = {}

    def create_publisher(self, msg_type, topic, depth):
        return self.publishers.setdefault(topic, _FakePublisher())


def test_combined_output_publishes_every_report_and_overall_on_one_topic() -> None:
    node = _FakeNode()
    report = TopicReport("/a", "t", "vitals", CheckStatus(severity=Severity.OK, label="x", message="ok"))
    overall = CheckStatus(severity=Severity.OK, label="overall", message="1 topic(s)")
    DiagnosticOutput(node, PublishConfig(per_plugin=False), []).publish("stamp", [report], overall)

    assert list(node.publishers) == ["/diagnostics"]
    published = node.publishers["/diagnostics"].published[0]
    assert [s.name for s in published.status] == ["testudo: /a", "testudo: overall"]


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
