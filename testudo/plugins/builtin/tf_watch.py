"""TF watch plugin: missing declared chains, multi-parent frames, and stale edges.

Bound to `/tf` (dynamic transforms), with `/tf_static` routed in as a
related topic feeding the same instance. Builds an undirected adjacency
graph from every observed (parent, child) edge to check connectivity for
declared frame pairs and to detect a child frame with more than one
distinct parent -- an invalid TF tree.

This approximates tf2's extrapolation-exception concept (a lookup outside
the buffered time range) as "this edge hasn't updated within tf2's default
~10s cache window", rather than running a real `tf2_ros.Buffer` -- which
maintains its own independent subscription outside Testudo's two-tier
model and would double-subscribe `/tf`.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from testudo.plugins.base import CheckPlugin, CheckStatus, Severity, ThresholdZone

#: tf2's default transform buffer duration; used here only as the
#: staleness window for the extrapolation-risk approximation.
DEFAULT_BUFFER_WINDOW_SECONDS = 10.0


class TFWatchPlugin(CheckPlugin):
    """Watches /tf (+/tf_static) for missing chains, multi-parent frames, and stale edges."""

    def __init__(
        self,
        thresholds: dict[str, ThresholdZone] | None = None,
        related_topics: dict[str, str] | None = None,
        buffer_window_seconds: float = DEFAULT_BUFFER_WINDOW_SECONDS,
    ) -> None:
        super().__init__(thresholds, related_topics)
        self._buffer_window_seconds = buffer_window_seconds
        self._watched_pairs: list[tuple[str, str]] = []
        self._parents_by_child: dict[str, set[str]] = defaultdict(set)
        self._adjacency: dict[str, set[str]] = defaultdict(set)
        self._last_seen_seconds: dict[tuple[str, str], float] = {}
        self._now = 0.0
        self._edge_count = 0

    @classmethod
    def msg_types(cls) -> tuple[str, ...]:
        return ("tf2_msgs/msg/TFMessage",)

    @classmethod
    def default_thresholds(cls) -> dict[str, ThresholdZone]:
        return {}

    def set_watched_pairs(self, pairs: list[tuple[str, str]]) -> None:
        """Declare which (parent, child) pairs to check for a connected chain.

        Called by SubscriptionManager (not part of the CheckPlugin ABC --
        TF's config comes from the dedicated `tf:` section, not a topic's
        thresholds/related_topics) with the pairs from config.tf.
        """
        self._watched_pairs = list(pairs)

    def on_tick(self, now_seconds: float) -> None:
        self._now = now_seconds

    def on_message(self, topic: str, msg: Any) -> None:
        for transform in msg.transforms:
            parent = transform.header.frame_id
            child = transform.child_frame_id
            self._edge_count += 1
            self._parents_by_child[child].add(parent)
            self._adjacency[parent].add(child)
            self._adjacency[child].add(parent)
            self._last_seen_seconds[(parent, child)] = self._now

    def _multi_parent_children(self) -> dict[str, set[str]]:
        return {child: parents for child, parents in self._parents_by_child.items() if len(parents) > 1}

    def _connected(self, a: str, b: str) -> bool:
        """Whether `b` is reachable from `a` by following observed edges in either direction."""
        if a not in self._adjacency or b not in self._adjacency:
            return False
        visited = {a}
        stack = [a]
        while stack:
            node = stack.pop()
            if node == b:
                return True
            for neighbor in self._adjacency[node]:
                if neighbor not in visited:
                    visited.add(neighbor)
                    stack.append(neighbor)
        return False

    def _missing_pairs(self) -> list[tuple[str, str]]:
        return [(parent, child) for parent, child in self._watched_pairs if not self._connected(parent, child)]

    def _stale_edges(self) -> list[tuple[str, str]]:
        return [
            edge
            for edge, last_seen in self._last_seen_seconds.items()
            if (self._now - last_seen) > self._buffer_window_seconds
        ]

    def get_status(self) -> CheckStatus:
        if self._edge_count == 0:
            return CheckStatus(severity=Severity.OK, label="tf", message="no transforms received yet")

        multi_parent = self._multi_parent_children()
        missing = self._missing_pairs()
        stale = self._stale_edges()

        severity = Severity.OK
        problems = []
        if multi_parent:
            severity = Severity.ERROR
            names = ", ".join(sorted(multi_parent))
            problems.append(f"multi-parent frame(s): {names}")
        if missing:
            severity = max(severity, Severity.ERROR)
            pairs = ", ".join(f"{parent}->{child}" for parent, child in missing)
            problems.append(f"missing chain(s): {pairs}")
        if stale:
            severity = max(severity, Severity.WARN)
            problems.append(f"{len(stale)} edge(s) not updated in >{self._buffer_window_seconds:.0f}s (extrapolation risk)")

        message = "; ".join(problems) if problems else "tf tree nominal"
        values = {
            "edge_count": str(len(self._last_seen_seconds)),
            "multi_parent_count": str(len(multi_parent)),
            "missing_pair_count": str(len(missing)),
            "stale_edge_count": str(len(stale)),
        }
        return CheckStatus(severity=severity, label="tf", message=message, values=values)
