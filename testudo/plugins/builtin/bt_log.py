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

from testudo.plugins.base import CheckPlugin, CheckStatus, Severity, ThresholdZone, colorize, evaluate_zone
from testudo.plugins.render import sparkline

_RUNNING = "RUNNING"
_FAILURE = "FAILURE"

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

#: Recovery triggers are binned per this many seconds for the sparkline,
#: keeping this many bins (10 minutes of history).
_TRIGGER_BIN_SECONDS = 60.0
_TRIGGER_BINS = 10

_BRANCH_SEPARATOR = " › "


def _recovery_type(node_name: str, recovery_node_prefixes: frozenset[str]) -> str | None:
    """The recovery prefix `node_name` is an instance of ("ClearEntireCostmap" for "ClearEntireCostmap-Local"), or None."""
    return next((prefix for prefix in sorted(recovery_node_prefixes) if node_name.startswith(prefix)), None)


def _ago(seconds: float) -> str:
    return f"{seconds:.0f}s ago" if seconds < 120 else f"{seconds / 60:.0f}m ago"


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
        # node name -> its uid (tree pre-order, so sorting by it puts
        # ancestors before descendants); arrival order where the message
        # has no uid field (older distros).
        self._running_nodes: dict[str, int] = {}
        self._last_branch: list[str] = []
        self._recovery_trigger_count = 0
        self._recovery_trigger_times: deque[float] = deque(maxlen=_TRIGGER_WINDOW_SIZE)
        self._recovery_counts: dict[str, int] = {}
        self._trigger_bins: deque[list[float]] = deque(maxlen=_TRIGGER_BINS)
        self._last_recovery: tuple[str, float] | None = None
        self._last_failure: tuple[str, float] | None = None

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
        first_failure: str | None = None
        for change in msg.event_log:
            self._event_count += 1
            node_name = change.node_name
            previous_status = change.previous_status
            current_status = change.current_status

            if current_status == _RUNNING:
                self._running_nodes[node_name] = getattr(change, "uid", self._event_count)
            else:
                self._running_nodes.pop(node_name, None)
            # A failing leaf fails its ancestors after it, in the same log:
            # the first FAILURE in a message is the one that explains the rest.
            if current_status == _FAILURE and first_failure is None:
                first_failure = node_name

            triggered = current_status == _RUNNING and previous_status != _RUNNING
            recovery = _recovery_type(node_name, self._recovery_node_prefixes) if triggered else None
            if recovery is not None:
                self._record_recovery(node_name, recovery)

        if first_failure is not None:
            self._last_failure = (first_failure, self._now)
        if self._running_nodes:
            self._last_branch = self._branch()

    def _record_recovery(self, node_name: str, recovery: str) -> None:
        self._recovery_trigger_count += 1
        self._recovery_trigger_times.append(self._now)
        self._recovery_counts[recovery] = self._recovery_counts.get(recovery, 0) + 1
        self._last_recovery = (node_name, self._now)
        bin_start = self._now - self._now % _TRIGGER_BIN_SECONDS
        if self._trigger_bins and self._trigger_bins[-1][0] == bin_start:
            self._trigger_bins[-1][1] += 1
        else:
            self._trigger_bins.append([bin_start, 1])

    def _branch(self) -> list[str]:
        """The RUNNING nodes, root first (by uid): the branch the tree is currently ticking."""
        return sorted(self._running_nodes, key=self._running_nodes.__getitem__)

    def _trigger_history(self) -> list[float]:
        """Recovery triggers per bin, oldest first, empty bins as 0.

        Starts at the first recorded bin rather than always spanning
        `_TRIGGER_BINS`, so a session younger than that doesn't read as
        minutes of calm before it even started.
        """
        if not self._trigger_bins:
            return []
        current_bin = self._now - self._now % _TRIGGER_BIN_SECONDS
        bins = min(_TRIGGER_BINS, int((current_bin - self._trigger_bins[0][0]) // _TRIGGER_BIN_SECONDS) + 1)
        counts = {start: count for start, count in self._trigger_bins}
        return [float(counts.get(current_bin - offset * _TRIGGER_BIN_SECONDS, 0)) for offset in range(bins - 1, -1, -1)]

    def _recovery_frequency_per_min(self) -> float | None:
        if len(self._recovery_trigger_times) < 2:
            return None
        span = self._recovery_trigger_times[-1] - self._recovery_trigger_times[0]
        if span <= 0:
            return None
        return (len(self._recovery_trigger_times) - 1) / span * 60.0

    def _metrics(self, frequency: float | None) -> dict[str, float | str]:
        """The published numbers: recovery rate and counts (total and per type), the running leaf, last events' ages."""
        metrics: dict[str, float | str] = {"recovery_count": self._recovery_trigger_count}
        if frequency is not None:
            metrics["recovery_frequency_per_min"] = frequency
        for name, count in sorted(self._recovery_counts.items()):
            metrics[f"recovery_count_{name}"] = count
        if self._running_nodes:
            metrics["running_leaf"] = self._branch()[-1]
        if self._recovery_trigger_count:
            last_name, last_at = self._last_recovery
            metrics["last_recovery"] = last_name
            metrics["last_recovery_age_s"] = self._now - last_at
        if self._last_failure is not None:
            name, at = self._last_failure
            metrics["last_failure"] = name
            metrics["last_failure_age_s"] = self._now - at
        return metrics

    def _running_line(self) -> str:
        """The running branch as a path with its leaf (what the robot is doing) in bold, or idle + the last one."""
        if self._running_nodes:
            *ancestors, leaf = self._branch()
            path = _BRANCH_SEPARATOR.join([*ancestors, f"[b]{leaf}[/b]"])
            return f"[green]▶[/green] {path}"
        if self._last_branch:
            return f"[dim]◌ idle · last: {_BRANCH_SEPARATOR.join(self._last_branch)}[/dim]"
        return "[dim]◌ idle[/dim]"

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
        codes = {"BT-001": colorize(f"recovering {frequency:.1f} times/min", severity)} if severity != Severity.OK else {}

        values = {"running": self._running_line()}
        if self._recovery_trigger_count:
            rate = colorize(f"{frequency:.1f}/min", severity) if frequency is not None else "[dim]–/min[/dim]"
            history = sparkline(self._trigger_history(), severity)
            last_name, last_at = self._last_recovery
            values["recoveries"] = f"{history} {rate}  [dim]last:[/dim] {last_name} {_ago(self._now - last_at)}"
            values["recovery_counts"] = " · ".join(
                f"{name} {count}" for name, count in sorted(self._recovery_counts.items(), key=lambda item: (-item[1], item[0]))
            )
        else:
            values["recoveries"] = "[dim]none yet[/dim]"
        if self._last_failure is not None:
            name, at = self._last_failure
            values["last_failure"] = f"{name}  [dim]{_ago(self._now - at)}[/dim]"

        column = colorize(f"{frequency:.1f}", severity) if frequency is not None else "[dim]–[/dim]"
        return CheckStatus(
            metrics=self._metrics(frequency),
            severity=severity,
            label="nav2-bt",
            message=message,
            values=values,
            topic_panel_column="Rec/m",
            topic_panel_value=column,
            codes=codes,
        )
