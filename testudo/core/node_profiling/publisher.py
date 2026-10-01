"""Publishes node-profiling DiagnosticArray on its own topic/timer.

Decoupled from both the main /diagnostics publish rate and any other
sampling cadence -- mirrors `core/publisher.DiagnosticPublisher`'s shape,
used the same way `DiagnosticPublisher` is: only where there's no
existing per-poll merge point to piggyback on (`cmd_check`'s fixed
observation window). `cmd_watch` instead publishes node stats manually
inside its own snapshot callback, alongside the main diagnostics array --
see cli.py.
"""
from __future__ import annotations

from diagnostic_msgs.msg import DiagnosticArray

from testudo.core.config import NodeProfilingConfig, SeverityMode
from testudo.core.node_profiling.profiler import NodeProfiler
from testudo.core.node_profiling.report import build_node_diagnostic_array


class NodeDiagnosticPublisher:
    """Owns a DiagnosticArray publisher and the fixed-rate timer driving it for node-profiling data."""

    def __init__(self, node, profiler: NodeProfiler, severity_mode: SeverityMode, config: NodeProfilingConfig) -> None:
        self._node = node
        self._profiler = profiler
        self._severity_mode = severity_mode
        self._publisher = node.create_publisher(DiagnosticArray, config.publish_topic, 10)
        period_seconds = 1.0 / config.publish_rate_hz if config.publish_rate_hz > 0 else 1.0
        self._timer = node.create_timer(period_seconds, self._publish_once)

    def _publish_once(self) -> None:
        self._profiler.tick()
        stats = list(self._profiler.reports())
        overall = self._profiler.overall_status(self._severity_mode)
        array = build_node_diagnostic_array(self._node.get_clock().now().to_msg(), stats, overall)
        self._publisher.publish(array)

    def destroy(self) -> None:
        """Stop the publish timer. Call before destroying the owning node."""
        self._timer.cancel()
        self._node.destroy_timer(self._timer)
