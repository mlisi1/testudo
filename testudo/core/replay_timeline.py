"""A replay sampled into frames along the bag's own time, for `replay --watch` to scrub through.

The bag is fed through exactly the pipeline `replay_bag` uses
(`replay.run_replay`); `FrameRecorder` just stops at every frame boundary
on the way, ticks the manager the way a live session's poll timer would,
and keeps what a live poll would have shown at that moment: every report,
the overall status, and the faults that cleared so far. The last frame is
the end of the bag, after the same final tick as the batch report.

Frames are at least `MIN_FRAME_INTERVAL_S` apart, spaced further on a long
bag so there are never more than `MAX_REPLAY_FRAMES` (each holds a full
set of reports). A fault shorter than one frame can fall between two
frames: the fault history only sees what a frame sees.
"""
from __future__ import annotations

from dataclasses import dataclass

import rclpy.node

from testudo.core.config import FaultsConfig, SeverityMode, TestudoConfig
from testudo.core.fault_history import ClearedFault, FaultEntry, FaultHistory
from testudo.core.replay import ReplayClockSource, bag_duration_s, run_replay
from testudo.core.subscription_manager import SubscriptionManager
from testudo.core.topic_report import TopicReport
from testudo.plugins.base import CheckStatus
from testudo.plugins.registry import DiscoveredPlugin

#: Frames are never closer than this: a live poll is 5 Hz, but a second is
#: as fine as scrubbing needs, and keeps a short bag's memory small.
MIN_FRAME_INTERVAL_S = 1.0

#: Upper bound on frames per replay, which spaces a long bag's frames out
#: (an hour-long bag gets one every 6 s).
MAX_REPLAY_FRAMES = 600


@dataclass(frozen=True)
class ReplayFrame:
    """What a live poll would have shown `offset_s` seconds into the bag.

    `cleared_faults` use the same offsets as their time base.
    """

    offset_s: float
    reports: list[TopicReport]
    overall: CheckStatus
    cleared_faults: tuple[ClearedFault, ...]


def frame_interval_for(duration_s: float | None) -> float:
    """Seconds between frames for a bag lasting `duration_s` (None: unknown, the minimum)."""
    if duration_s is None or duration_s <= 0:
        return MIN_FRAME_INTERVAL_S
    return max(MIN_FRAME_INTERVAL_S, duration_s / MAX_REPLAY_FRAMES)


class FrameRecorder:
    """A `replay.BagTimeHook` that records a frame every `interval_s` of bag time."""

    def __init__(
        self,
        interval_s: float,
        severity_mode: SeverityMode,
        faults: FaultsConfig | None = None,
        keep_frames: bool = True,
    ) -> None:
        """`keep_frames=False` keeps only the latest frame (a batch report needs no timeline)."""
        faults = faults or FaultsConfig()
        self._keep_frames = keep_frames
        self._interval_ns = int(interval_s * 1e9)
        self._severity_mode = severity_mode
        self._start_ns: int | None = None
        self._next_ns = 0
        self._history = FaultHistory(
            retention=faults.retention, recent_window_seconds=faults.recent_window_seconds
        )
        self._clock_source: ReplayClockSource | None = None
        self.frames: list[ReplayFrame] = []

    def __call__(self, manager: SubscriptionManager, clock_source: ReplayClockSource, timestamp_ns: int) -> None:
        """Record every boundary up to `timestamp_ns`, before the message stamped with it is fed."""
        self._clock_source = clock_source
        if self._start_ns is None:
            self._start_ns = timestamp_ns
            self._next_ns = timestamp_ns + self._interval_ns
            return
        while timestamp_ns >= self._next_ns:
            clock_source.advance_to(self._next_ns)
            manager.tick()
            self._record(manager, self._next_ns)
            self._next_ns += self._interval_ns

    def finish(self, manager: SubscriptionManager) -> None:
        """Record the end-of-bag frame (`manager` already had its final tick)."""
        end_ns = self._clock_source.nanoseconds if self._clock_source is not None else 0
        self._record(manager, end_ns)

    def _record(self, manager: SubscriptionManager, at_ns: int) -> None:
        offset_s = (at_ns - (self._start_ns or at_ns)) / 1e9
        reports = manager.reports()
        self._history.update(reports, offset_s)
        overall = manager.overall_status(self._severity_mode)
        frame = ReplayFrame(offset_s, reports, overall, self._history.cleared())
        if self._keep_frames:
            self.frames.append(frame)
        else:
            self.frames = [frame]

    def fault_entries(self) -> list[FaultEntry]:
        """Every fault the bag raised, as of the last frame (offsets into the bag as times)."""
        now = self.frames[-1].offset_s if self.frames else 0.0
        return self._history.entries(now)


def replay_with_faults(
    bag_path: str,
    config: TestudoConfig,
    discovered_plugins: list[DiscoveredPlugin],
    node: rclpy.node.Node,
) -> tuple[ReplayFrame, list[FaultEntry]]:
    """The end-of-bag frame plus every fault raised along the way: what `replay`'s text report prints.

    Samples the bag like `replay_timeline` (so a fault is seen the way
    `replay --watch` and a live session would see it), keeping only the
    latest frame.

    Raises:
        FileNotFoundError: `bag_path` doesn't exist or isn't a readable bag.
    """
    recorder = FrameRecorder(
        frame_interval_for(bag_duration_s(bag_path)), config.severity_mode, config.faults, keep_frames=False
    )
    manager = run_replay(bag_path, config, discovered_plugins, node, on_bag_time=recorder)
    recorder.finish(manager)
    return recorder.frames[-1], recorder.fault_entries()


def replay_timeline(
    bag_path: str,
    config: TestudoConfig,
    discovered_plugins: list[DiscoveredPlugin],
    node: rclpy.node.Node,
) -> list[ReplayFrame]:
    """Replay `bag_path` into frames, oldest first; the last one is the end of the bag.

    Raises:
        FileNotFoundError: `bag_path` doesn't exist or isn't a readable bag.
    """
    recorder = FrameRecorder(frame_interval_for(bag_duration_s(bag_path)), config.severity_mode, config.faults)
    manager = run_replay(bag_path, config, discovered_plugins, node, on_bag_time=recorder)
    recorder.finish(manager)
    return recorder.frames
