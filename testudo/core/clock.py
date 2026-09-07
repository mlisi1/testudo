"""use_sim_time-aware time source for staleness/frequency checks.

rclpy.node.Node already wires this up for free: its constructor attaches a
`TimeSource` that auto-declares the `use_sim_time` parameter and switches
the node's clock onto `/clock` whenever that parameter is True
(rclpy/time_source.py). This module just exposes the thin slice of that
Testudo's checks actually need — `now_seconds()` and `is_sim_time_active()`
— behind an interface that doesn't require a live `rclpy.node.Node` to test.
"""
from __future__ import annotations

from typing import Callable, Protocol


class _ClockLike(Protocol):
    def now(self) -> object:
        ...  # returns an rclpy.time.Time-like object with `.nanoseconds`


class TestudoClock:
    """Wraps a ROS clock plus a query for whether sim time is active."""

    def __init__(self, clock: _ClockLike, sim_time_query: Callable[[], bool]) -> None:
        self._clock = clock
        self._sim_time_query = sim_time_query

    @classmethod
    def from_node(cls, node: object) -> "TestudoClock":
        """Build a TestudoClock from a live rclpy Node.

        Relies on `node.get_clock()` already returning sim time when
        `use_sim_time` is True, and on `use_sim_time` having been
        auto-declared by rclpy's TimeSource attachment.
        """
        return cls(
            clock=node.get_clock(),  # type: ignore[attr-defined]
            sim_time_query=lambda: bool(node.get_parameter("use_sim_time").value),  # type: ignore[attr-defined]
        )

    def now_seconds(self) -> float:
        """Current time in seconds, from `/clock` if sim time is active, else wall time."""
        return self._clock.now().nanoseconds / 1e9  # type: ignore[attr-defined]

    def is_sim_time_active(self) -> bool:
        """Whether this clock is currently keyed off `/clock` rather than wall time."""
        return self._sim_time_query()
