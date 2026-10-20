"""Publishes DiagnosticArray, decoupled from each check's own sampling rate.

Interoperable with rqt_robot_monitor, Foxglove, PlotJuggler, and
bag-recordable for free -- the point of publishing the standard
diagnostic_msgs/DiagnosticArray instead of a custom message type. Runs on
its own fixed-rate timer (config.publish.rate_hz): a 200 Hz IMU's checks
don't publish 200 times a second, and a topic with no new sample since the
last tick still gets republished with its last-known status, matching
standard diagnostic_updater behavior.

By default (`publish.per_plugin`) the reports are split by the plugin that
checks them, one topic each (`/testudo/odometry`, ...), so a consumer
subscribes to only what it needs; `per_plugin: false` keeps everything on
the single `publish.topic` that rqt_robot_monitor/diagnostic_aggregator
read (see `DiagnosticOutput`).
"""
from __future__ import annotations

from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue

from testudo.core.config import PublishConfig, SeverityMode
from testudo.core.plain_values import published_values, strip_markup
from testudo.core.subscription_manager import SubscriptionManager
from testudo.core.topic_report import TopicReport
from testudo.plugins.base import CheckStatus
from testudo.plugins.registry import DiscoveredPlugin

#: Used as both the diagnostic name prefix ("testudo: /odom") and hardware_id,
#: the conventional diagnostic_updater grouping scheme rqt_robot_monitor expects.
NAME_PREFIX = "testudo"

#: Per-plugin mode's topic for reports no plugin checks (vitals tier,
#: publisher-less and excluded topics), and the one for the overall roll-up.
VITALS_GROUP = "vitals"
OVERALL_GROUP = "overall"

#: Tiers whose report belongs to the plugin covering its message type: a
#: presence-tier camera is still that plugin's topic, just not subscribed.
_PLUGIN_TIERS = frozenset({"full", "presence"})


def to_diagnostic_status(name: str, status: CheckStatus) -> DiagnosticStatus:
    """Convert one CheckStatus into a diagnostic_msgs/DiagnosticStatus."""
    diagnostic = DiagnosticStatus()
    diagnostic.name = f"{NAME_PREFIX}: {name}"
    diagnostic.level = bytes([status.severity])
    diagnostic.message = strip_markup(status.message)
    diagnostic.hardware_id = NAME_PREFIX
    diagnostic.values = [KeyValue(key=key, value=value) for key, value in published_values(status).items()]
    return diagnostic


def build_diagnostic_array(stamp, reports: list[TopicReport], overall: CheckStatus) -> DiagnosticArray:
    """Build one DiagnosticArray: one status per report, plus an "overall" summary status."""
    array = DiagnosticArray()
    array.header.stamp = stamp
    array.status = [to_diagnostic_status(report.topic, report.status) for report in reports]
    array.status.append(to_diagnostic_status("overall", overall))
    return array


def plugin_names_by_msg_type(plugins: list[DiscoveredPlugin]) -> dict[str, str]:
    """Each covered message type's plugin name; first one found wins on overlap, as in the subscription manager."""
    names: dict[str, str] = {}
    for plugin in plugins:
        for msg_type in plugin.plugin_class.msg_types():
            names.setdefault(msg_type, plugin.name)
    return names


def group_by_plugin(reports: list[TopicReport], plugin_names: dict[str, str]) -> dict[str, list[TopicReport]]:
    """`reports` keyed by the plugin checking each one, or `VITALS_GROUP` when none does."""
    groups: dict[str, list[TopicReport]] = {}
    for report in reports:
        name = plugin_names.get(report.msg_type) if report.tier in _PLUGIN_TIERS else None
        groups.setdefault(name or VITALS_GROUP, []).append(report)
    return groups


class DiagnosticOutput:
    """Owns the DiagnosticArray publisher(s) and routes each publish to them.

    Per-plugin mode creates a plugin's topic the first time that plugin has
    a report, then keeps publishing to it every time, an empty array once
    its topics are gone (e.g. the stack was closed), so a subscriber sees
    them disappear rather than a last message frozen in place.
    """

    def __init__(self, node, publish_config: PublishConfig, plugins: list[DiscoveredPlugin]) -> None:
        self._node = node
        self._config = publish_config
        self._plugin_names = plugin_names_by_msg_type(plugins)
        self._publishers: dict[str, object] = {}
        if not publish_config.per_plugin:
            self._publishers[publish_config.topic] = node.create_publisher(DiagnosticArray, publish_config.topic, 10)

    def topic_for(self, group: str) -> str:
        """The topic a per-plugin group publishes on."""
        return f"{self._config.namespace}/{group}"

    def publish(self, stamp, reports: list[TopicReport], overall: CheckStatus) -> None:
        """Publish one round: everything on the combined topic, or one array per plugin plus the overall."""
        if not self._config.per_plugin:
            self._publishers[self._config.topic].publish(build_diagnostic_array(stamp, reports, overall))
            return
        arrays: dict[str, DiagnosticArray] = {}
        for group, group_reports in group_by_plugin(reports, self._plugin_names).items():
            arrays[self.topic_for(group)] = _array(stamp, [to_diagnostic_status(r.topic, r.status) for r in group_reports])
        arrays[self.topic_for(OVERALL_GROUP)] = _array(stamp, [to_diagnostic_status("overall", overall)])
        for topic in self._publishers.keys() - arrays.keys():
            arrays[topic] = _array(stamp, [])
        for topic, array in arrays.items():
            if topic not in self._publishers:
                self._publishers[topic] = self._node.create_publisher(DiagnosticArray, topic, 10)
            self._publishers[topic].publish(array)


def _array(stamp, statuses: list[DiagnosticStatus]) -> DiagnosticArray:
    array = DiagnosticArray()
    array.header.stamp = stamp
    array.status = statuses
    return array


class DiagnosticPublisher:
    """Owns a DiagnosticArray publisher and the fixed-rate timer driving it.

    Each tick also calls `manager.tick()` first, so full-tier plugins'
    time-based state (on_tick) advances at this same decoupled rate even
    between message arrivals -- not just when a message happens to trigger
    a decimated content-check.
    """

    def __init__(
        self,
        node,
        manager: SubscriptionManager,
        severity_mode: SeverityMode,
        publish_config: PublishConfig,
        plugins: list[DiscoveredPlugin],
    ) -> None:
        self._node = node
        self._manager = manager
        self._severity_mode = severity_mode
        self._output = DiagnosticOutput(node, publish_config, plugins)
        rate_hz = publish_config.rate_hz
        period_seconds = 1.0 / rate_hz if rate_hz > 0 else 1.0
        self._timer = node.create_timer(period_seconds, self._publish_once)

    def _publish_once(self) -> None:
        self._manager.tick()
        reports = self._manager.reports()
        overall = self._manager.overall_status(self._severity_mode)
        self._output.publish(self._node.get_clock().now().to_msg(), reports, overall)

    def destroy(self) -> None:
        """Stop the publish timer. Call before destroying the owning node."""
        self._timer.cancel()
        self._node.destroy_timer(self._timer)
