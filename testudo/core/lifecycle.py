"""Lifecycle node state tracking.

Kept as a pure state machine -- fed state labels from wherever they're
observed (a `~/transition_event` subscription, in practice) -- so it's
testable without a live ROS graph and independent of how those events are
delivered.
"""
from __future__ import annotations

#: States a topic's owning node can be in where its diagnostics should be
#: suppressed rather than flagged: the node isn't actively running yet or
#: anymore, so silence or staleness there is expected, not a fault.
SUPPRESSED_STATES = frozenset({"unconfigured", "inactive", "finalized"})


class LifecycleTracker:
    """Tracks the current state label of every known lifecycle node."""

    def __init__(self) -> None:
        self._state_by_node: dict[str, str] = {}

    def on_transition_event(self, node_name: str, goal_state_label: str) -> None:
        """Record `node_name`'s new state, from its `~/transition_event` topic."""
        self._state_by_node[node_name] = goal_state_label

    def state_of(self, node_name: str) -> str | None:
        """The last-known state label for `node_name`, or None if never observed."""
        return self._state_by_node.get(node_name)

    def is_suppressed(self, node_name: str) -> bool:
        """Whether diagnostics for a topic owned by `node_name` should be suppressed right now."""
        return self._state_by_node.get(node_name) in SUPPRESSED_STATES
