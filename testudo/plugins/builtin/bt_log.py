"""Nav2 behavior-tree log plugin.

Tracks nav2_msgs/msg/BehaviorTreeLog, the stream of every BT node's
IDLE/RUNNING/SUCCESS/FAILURE status transitions Nav2's BT navigator
publishes on `/behavior_tree_log` whenever a node's status actually
changes (not once per tick). Two things fall out of that log that
Nav2GoalPlugin's action-status tracking can't see:

- which node(s) are currently RUNNING -- BT.CPP keeps every ancestor of the
  currently-ticking leaf (a Sequence, a Fallback, a RecoveryNode, ...)
  RUNNING simultaneously while that leaf executes, so this is normally more
  than one node at a time, not a single "current state".
- how often a *recovery* node actually triggers -- including ones wired
  directly into the tree as a synchronous service call (e.g.
  ClearEntireCostmap) rather than exposed as their own action server, which
  is the only kind Nav2GoalPlugin's recovery-frequency tracking (via
  /spin, /backup, /wait's own status topics) can ever see.
"""
from __future__ import annotations

from collections import deque
from typing import Any

from testudo.plugins.base import CheckPlugin, CheckStatus, Severity, ThresholdZone, evaluate_zone

_RUNNING = "RUNNING"

#: Nav2's default recovery-behavior BT node types. BT.CPP suffixes an
#: instance name when more than one node of the same type appears in a
#: tree (e.g. "ClearEntireCostmap-Context"), so this is a set of prefixes
#: matched with `str.startswith`, not an exact-name set.
DEFAULT_RECOVERY_NODE_PREFIXES: frozenset[str] = frozenset(
    {
        "Spin",
        "BackUp",
        "Wait",
        "DriveOnHeading",
        "ClearEntireCostmap",
        "ClearCostmapExceptRegion",
        "ClearCostmapAroundRobot",
    }
)

_TRIGGER_WINDOW_SIZE = 20


def _is_recovery_node(node_name: str, recovery_node_prefixes: frozenset[str]) -> bool:
    return any(node_name.startswith(prefix) for prefix in recovery_node_prefixes)


class BehaviorTreeLogPlugin(CheckPlugin):
    """Currently-active BT node tracking and recovery-node trigger frequency."""

    def __init__(
        self,
        thresholds: dict[str, ThresholdZone] | None = None,
        related_topics: dict[str, str] | None = None,
        recovery_node_prefixes: frozenset[str] = DEFAULT_RECOVERY_NODE_PREFIXES,
    ) -> None:
        super().__init__(thresholds, related_topics)
        self._recovery_node_prefixes = recovery_node_prefixes
        self._now = 0.0
        self._event_count = 0
        self._running_nodes: set[str] = set()
        self._recovery_trigger_count = 0
        self._recovery_trigger_times: deque[float] = deque(maxlen=_TRIGGER_WINDOW_SIZE)
        self._last_recovery_node: str | None = None

    @classmethod
    def msg_types(cls) -> tuple[str, ...]:
        return ("nav2_msgs/msg/BehaviorTreeLog",)

    @classmethod
    def default_thresholds(cls) -> dict[str, ThresholdZone]:
        # No sane universal default: how often recovery should legitimately
        # trigger depends entirely on the environment and BT design.
        return {}

    def on_tick(self, now_seconds: float) -> None:
        self._now = now_seconds

    def on_message(self, topic: str, msg: Any) -> None:
        for change in msg.event_log:
            self._event_count += 1
            node_name = change.node_name
            previous_status = change.previous_status
            current_status = change.current_status

            if current_status == _RUNNING:
                self._running_nodes.add(node_name)
            else:
                self._running_nodes.discard(node_name)

            triggered = current_status == _RUNNING and previous_status != _RUNNING
            if triggered and _is_recovery_node(node_name, self._recovery_node_prefixes):
                self._recovery_trigger_count += 1
                self._recovery_trigger_times.append(self._now)
                self._last_recovery_node = node_name

    def _recovery_frequency_per_min(self) -> float | None:
        if len(self._recovery_trigger_times) < 2:
            return None
        span = self._recovery_trigger_times[-1] - self._recovery_trigger_times[0]
        if span <= 0:
            return None
        return (len(self._recovery_trigger_times) - 1) / span * 60.0

    def get_status(self) -> CheckStatus:
        if self._event_count == 0:
            return CheckStatus(severity=Severity.OK, label="nav2-bt", message="no behavior tree log received yet")

        frequency = self._recovery_frequency_per_min()
        severity = (
            Severity.OK
            if frequency is None
            else evaluate_zone(frequency, self.thresholds.get("recovery_frequency_per_min"))
        )
        message = f"recovery frequency {frequency:.1f}/min" if severity != Severity.OK else "nominal"

        active_nodes = ", ".join(sorted(self._running_nodes)) if self._running_nodes else "none"
        values = {
            "active_nodes": active_nodes,
            "active_node_count": str(len(self._running_nodes)),
            "recovery_trigger_count": str(self._recovery_trigger_count),
            "recovery_frequency_per_min": f"{frequency:.3g}" if frequency is not None else "n/a",
            "last_recovery_node": self._last_recovery_node or "n/a",
        }
        return CheckStatus(
            severity=severity,
            label="nav2-bt",
            message=message,
            values=values,
            topic_panel_column="Active",
            topic_panel_value=str(len(self._running_nodes)),
        )
