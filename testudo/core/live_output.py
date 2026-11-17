"""What a live session (`watch`, with or without its TUI) publishes after each poll."""
from __future__ import annotations

from typing import TYPE_CHECKING

from diagnostic_msgs.msg import DiagnosticArray

from testudo.core.config import TestudoConfig
from testudo.core.fault_history import FaultHistory
from testudo.core.fault_output import build_faults_array
from testudo.core.node_profiling.report import build_node_diagnostic_array
from testudo.core.publisher import DiagnosticOutput
from testudo.core.topic_report import TopicReport
from testudo.plugins.base import CheckStatus
from testudo.plugins.registry import DiscoveredPlugin

if TYPE_CHECKING:
    from testudo.core.node_profiling.profiler import NodeProfiler


class LiveOutput:
    """Everything a live session publishes after each poll: topic diagnostics, fault history, node profiling.

    Driven by whatever polls (the TUI's timer, or `watch --headless`'s
    loop), never by a timer of its own: node profiling's `tick()` already
    ran in that same poll (`LiveDataSource.poll`), so publishing here keeps
    every writer to the profiler on one thread.
    """

    def __init__(
        self,
        node,
        config: TestudoConfig,
        plugins: list[DiscoveredPlugin],
        node_profiler: "NodeProfiler | None" = None,
        fault_history: FaultHistory | None = None,
    ) -> None:
        self._node = node
        self._fault_history = fault_history
        self._faults_publisher = (
            node.create_publisher(DiagnosticArray, config.faults.publish_topic, 10) if fault_history is not None else None
        )
        self._severity_mode = config.severity_mode
        self._topics = DiagnosticOutput(node, config.publish, plugins)
        self._node_profiler = node_profiler
        self._node_publisher = (
            node.create_publisher(DiagnosticArray, config.node_profiling.publish_topic, 10)
            if node_profiler is not None
            else None
        )
        # The NodeProfiler.reports() tuple last published: it samples every
        # `poll_interval_seconds`, not every poll, and swaps in a new tuple
        # each time it does -- an unchanged tuple means nothing new to send.
        self._last_node_stats: tuple | None = None

    def publish(self, reports: list[TopicReport], overall: CheckStatus, now: float) -> None:
        """Publish one poll's `reports` (node-profiling rows included) and topic-only `overall`.

        `now` is the poll's time on the fault history's own time base (the
        snapshot's `time_s`), so an active fault's `duration_s` is current.
        """
        stamp = self._node.get_clock().now().to_msg()
        # Node rows are merged into the reports for the TUI's panes; the
        # topic diagnostics stay topic-only, node rows go on their own topic.
        self._topics.publish(stamp, [report for report in reports if report.tier != "node"], overall)
        if self._faults_publisher is not None and self._fault_history is not None:
            self._faults_publisher.publish(build_faults_array(stamp, self._fault_history.entries(now)))
        if self._node_publisher is None or self._node_profiler is None:
            return
        node_stats = self._node_profiler.reports()
        if node_stats is self._last_node_stats:
            return
        self._last_node_stats = node_stats
        overall_nodes = self._node_profiler.overall_status(self._severity_mode)
        self._node_publisher.publish(build_node_diagnostic_array(stamp, list(node_stats), overall_nodes))
