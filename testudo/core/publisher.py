"""Publishes DiagnosticArray, decoupled from each check's own sampling rate.

Interoperable with rqt_robot_monitor, Foxglove, PlotJuggler, and
bag-recordable for free -- the point of publishing the standard
diagnostic_msgs/DiagnosticArray instead of a custom message type. Runs on
its own fixed-rate timer (config.publish.rate_hz): a 200 Hz IMU's checks
don't publish 200 times a second, and a topic with no new sample since the
last tick still gets republished with its last-known status, matching
standard diagnostic_updater behavior.
"""
from __future__ import annotations

from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue

from testudo.core.config import SeverityMode
from testudo.core.subscription_manager import SubscriptionManager
from testudo.core.topic_report import TopicReport
from testudo.plugins.base import CheckStatus

#: Used as both the diagnostic name prefix ("testudo: /odom") and hardware_id,
#: the conventional diagnostic_updater grouping scheme rqt_robot_monitor expects.
NAME_PREFIX = "testudo"


def to_diagnostic_status(name: str, status: CheckStatus) -> DiagnosticStatus:
    """Convert one CheckStatus into a diagnostic_msgs/DiagnosticStatus."""
    diagnostic = DiagnosticStatus()
    diagnostic.name = f"{NAME_PREFIX}: {name}"
    diagnostic.level = bytes([status.severity])
    diagnostic.message = status.message
    diagnostic.hardware_id = NAME_PREFIX
    diagnostic.values = [KeyValue(key=key, value=value) for key, value in status.values.items()]
    return diagnostic


def build_diagnostic_array(stamp, reports: list[TopicReport], overall: CheckStatus) -> DiagnosticArray:
    """Build one DiagnosticArray: one status per report, plus an "overall" summary status."""
    array = DiagnosticArray()
    array.header.stamp = stamp
    array.status = [to_diagnostic_status(report.topic, report.status) for report in reports]
    array.status.append(to_diagnostic_status("overall", overall))
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
        topic: str,
        rate_hz: float,
    ) -> None:
        self._node = node
        self._manager = manager
        self._severity_mode = severity_mode
        self._publisher = node.create_publisher(DiagnosticArray, topic, 10)
        period_seconds = 1.0 / rate_hz if rate_hz > 0 else 1.0
        self._timer = node.create_timer(period_seconds, self._publish_once)

    def _publish_once(self) -> None:
        self._manager.tick()
        reports = self._manager.reports()
        overall = self._manager.overall_status(self._severity_mode)
        array = build_diagnostic_array(self._node.get_clock().now().to_msg(), reports, overall)
        self._publisher.publish(array)

    def destroy(self) -> None:
        """Stop the publish timer. Call before destroying the owning node."""
        self._timer.cancel()
        self._node.destroy_timer(self._timer)
