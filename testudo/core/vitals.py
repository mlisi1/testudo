"""Vitals tier: rolling liveness/frequency stats gathered without deserializing messages.

This is the fallback tier for every topic Testudo has no content-check
plugin for: a raw subscription callback receives serialized bytes and does
nothing but timestamp arrival, so watching 137 topics stays cheap.

rclpy has no Python equivalent of rclcpp's `topic_stats_options` /
`libstatistics_collector` — that feature is C++-only. This module is the
hand-rolled equivalent: a bounded rolling window of arrival timestamps.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

DEFAULT_WINDOW_SIZE = 20
DEFAULT_STALE_AFTER_SECONDS = 2.0


@dataclass
class TopicVitals:
    """Rolling liveness/frequency stats for one topic."""

    topic: str
    window_size: int = DEFAULT_WINDOW_SIZE
    message_count: int = 0
    _arrival_times: deque[float] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._arrival_times = deque(maxlen=self.window_size)

    def record_arrival(self, now_seconds: float) -> None:
        """Record a message arrival at `now_seconds`."""
        self.message_count += 1
        self._arrival_times.append(now_seconds)

    def reset(self) -> None:
        """Clear the accumulated count and rolling arrival-time window (the TUI's 'reset stats')."""
        self.message_count = 0
        self._arrival_times.clear()

    @property
    def last_arrival_seconds(self) -> float | None:
        """Timestamp of the most recent arrival, or None if none has arrived yet."""
        return self._arrival_times[-1] if self._arrival_times else None

    def age_seconds(self, now_seconds: float) -> float | None:
        """Seconds since the last message arrived, or None if none has arrived yet."""
        if self.last_arrival_seconds is None:
            return None
        return now_seconds - self.last_arrival_seconds

    def rate_hz(self) -> float | None:
        """Mean rate over the rolling window, or None with fewer than 2 samples in it."""
        if len(self._arrival_times) < 2:
            return None
        span = self._arrival_times[-1] - self._arrival_times[0]
        if span <= 0:
            return None
        return (len(self._arrival_times) - 1) / span

    def mean_period_seconds(self) -> float | None:
        """Mean inter-arrival period over the rolling window, or None with fewer than 2 samples."""
        rate = self.rate_hz()
        return None if rate is None else 1.0 / rate
