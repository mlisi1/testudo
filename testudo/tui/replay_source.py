"""`replay --watch`: a replayed bag's frames, played back and scrubbed through in the TUI.

`ReplayTimeline` is the playback state (which frame, playing or not),
free of Textual so it's testable on its own; `ReplayDataSource` serves the
current frame to the app as a `WatchDataSource`. It opens on the last
frame, paused: the same end-of-bag verdict the batch report gives, with
the rest of the bag one keypress away.
"""
from __future__ import annotations

from testudo.core.config import ExcludeRule
from testudo.core.replay_timeline import ReplayFrame
from testudo.tui.data_source import WatchSnapshot


class ReplayTimeline:
    """Playback over `frames` (oldest first, at least one) in bag time."""

    def __init__(self, frames: list[ReplayFrame]) -> None:
        if not frames:
            raise ValueError("a replay timeline needs at least one frame")
        self._frames = frames
        self._index = len(frames) - 1
        self._position_s = frames[-1].offset_s
        self.playing = False

    @property
    def frame(self) -> ReplayFrame:
        return self._frames[self._index]

    @property
    def index(self) -> int:
        return self._index

    @property
    def frame_count(self) -> int:
        return len(self._frames)

    @property
    def position_s(self) -> float:
        """Seconds into the bag of the frame on show."""
        return self.frame.offset_s

    @property
    def duration_s(self) -> float:
        return self._frames[-1].offset_s

    def step(self, frames: int) -> bool:
        """Move `frames` frames (negative: back), stopping playback; True if the frame changed."""
        self.playing = False
        return self._seek_index(self._index + frames)

    def toggle_play(self) -> None:
        """Play or pause; playing from the last frame starts over from the first."""
        if not self.playing and self._index == len(self._frames) - 1:
            self._seek_index(0)
        self.playing = not self.playing

    def advance(self, elapsed_s: float) -> bool:
        """Move on by `elapsed_s` of bag time while playing (1x); True if the frame changed.

        Stops on the last frame.
        """
        if not self.playing:
            return False
        self._position_s += elapsed_s
        index = self._index
        while index + 1 < len(self._frames) and self._frames[index + 1].offset_s <= self._position_s:
            index += 1
        if index == len(self._frames) - 1:
            self.playing = False
        changed = index != self._index
        self._index = index
        return changed

    def _seek_index(self, index: int) -> bool:
        index = max(0, min(len(self._frames) - 1, index))
        changed = index != self._index
        self._index = index
        self._position_s = self._frames[index].offset_s
        return changed


class ReplayDataSource:
    """The timeline's current frame as a `WatchDataSource`.

    Exclude rules are read-only, as in `StaticDataSource`: every frame is
    already computed, so nothing live exists to unsubscribe, and there's
    no config file bound to persist a change into.
    """

    def __init__(self, frames: list[ReplayFrame], exclude_rules: list[ExcludeRule] | None = None) -> None:
        self.timeline = ReplayTimeline(frames)
        self._exclude_rules = list(exclude_rules) if exclude_rules is not None else []

    def poll(self) -> WatchSnapshot:
        frame = self.timeline.frame
        return WatchSnapshot(frame.reports, frame.overall, frame.cleared_faults, frame.offset_s)

    def reset_stats(self) -> None:
        pass  # every frame is already computed: nothing accumulates to reset

    def is_live(self) -> bool:
        return False

    def exclude_rules(self) -> list[ExcludeRule]:
        return self._exclude_rules

    def add_exclude_rule(self, rule: ExcludeRule) -> int | None:
        return None

    def remove_exclude_rule(self, rule: ExcludeRule) -> bool:
        return False
