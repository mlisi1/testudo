"""The velocity command plugin's checks over time: pure state, fed by `VelocityCommandPlugin`."""
from __future__ import annotations

from collections import deque

from testudo.plugins.builtin.localization_motion import CMD_MOVING_THRESHOLD_MPS, CMD_MOVING_THRESHOLD_RADPS

#: A command or odometry message older than this no longer says anything.
EXPIRY_SECONDS = 1.0
#: The robot responds once it reaches this share of the commanded speed...
_RESPONSE_RATIO = 0.1
#: ...and at least these speeds, below which odometry is only noise.
_RESPONSE_FLOOR_MPS = 0.01
_RESPONSE_FLOOR_RADPS = 0.01
#: Sign reversals are counted over this window.
REVERSAL_WINDOW_SECONDS = 10.0


def _responds(commanded: float, reported: float, moving_above: float, floor: float) -> bool:
    """True when one axis is commanded to move and odometry shows it doing so."""
    return abs(commanded) > moving_above and abs(reported) >= max(floor, _RESPONSE_RATIO * abs(commanded))


class StuckCheck:
    """How long the robot has been told to move while odometry says it isn't."""

    def __init__(self) -> None:
        self._command: tuple[float, float] = (0.0, 0.0)
        self._last_command: float | None = None
        self._reported: tuple[float, float] = (0.0, 0.0)
        self._last_odometry: float | None = None
        self._stuck_since: float | None = None

    def on_command(self, linear: float, angular: float, now: float) -> None:
        """Take one velocity command."""
        self._command, self._last_command = (linear, angular), now

    def on_odometry(self, linear: float, angular: float, now: float) -> None:
        """Take the speeds one odometry message reports."""
        self._reported, self._last_odometry = (linear, angular), now

    def _is_stuck(self, now: float) -> bool:
        if self._last_command is None or now - self._last_command > EXPIRY_SECONDS:
            return False
        if self._last_odometry is None or now - self._last_odometry > EXPIRY_SECONDS:
            return False  # no odometry to judge by, which says nothing about the robot
        linear, angular = self._command
        if abs(linear) <= CMD_MOVING_THRESHOLD_MPS and abs(angular) <= CMD_MOVING_THRESHOLD_RADPS:
            return False
        return not (
            _responds(linear, self._reported[0], CMD_MOVING_THRESHOLD_MPS, _RESPONSE_FLOOR_MPS)
            or _responds(angular, self._reported[1], CMD_MOVING_THRESHOLD_RADPS, _RESPONSE_FLOOR_RADPS)
        )

    def update(self, now: float, judged: bool) -> None:
        """Advance to `now`; `judged` is False while no goal is running on a topic that watches goals."""
        if not judged or not self._is_stuck(now):
            self._stuck_since = None
        elif self._stuck_since is None:
            self._stuck_since = now

    def seconds(self, now: float) -> float:
        """How long the robot has been stuck, 0 when it isn't."""
        return 0.0 if self._stuck_since is None else now - self._stuck_since


class ReversalRate:
    """Sign reversals per second of one command axis: a controller hunting left and right."""

    def __init__(self, deadband: float) -> None:
        self._deadband = deadband
        self._sign = 0
        self._reversals: deque[float] = deque()

    def observe(self, value: float, now: float) -> None:
        """Take one command value; values inside the deadband keep the previous sign."""
        if abs(value) > self._deadband:
            sign = 1 if value > 0 else -1
            if self._sign == -sign:
                self._reversals.append(now)
            self._sign = sign

    def per_second(self, now: float) -> float:
        """Reversals per second over the last `REVERSAL_WINDOW_SECONDS`."""
        while self._reversals and now - self._reversals[0] > REVERSAL_WINDOW_SECONDS:
            self._reversals.popleft()
        return len(self._reversals) / REVERSAL_WINDOW_SECONDS


class PinnedAxis:
    """How long one command axis has held exactly the same non-zero value: clipped at a limit."""

    def __init__(self, deadband: float) -> None:
        self._deadband = deadband
        self._value: float | None = None
        self._since = 0.0
        self._last = 0.0

    def observe(self, value: float, now: float) -> None:
        """Take one command value."""
        self._last = now
        if abs(value) <= self._deadband:
            self._value = None
        elif value != self._value:
            self._value, self._since = value, now

    def seconds(self, now: float) -> float:
        """How long the value has been pinned, 0 when it isn't or the commands stopped."""
        if self._value is None or now - self._last > EXPIRY_SECONDS:
            return 0.0
        return now - self._since

    @property
    def value(self) -> float:
        """The pinned value (0 when none)."""
        return self._value or 0.0
