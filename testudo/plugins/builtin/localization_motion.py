"""Whether a robot is being told to move, from its `cmd_vel`.

A global pose such as AMCL's is only published while the robot moves, so
its silence is normal at rest and a fault in motion. Telling the two apart
takes a `cmd_vel` related topic; without one, silence is given the benefit
of the doubt.
"""
from __future__ import annotations

#: Commanded speeds above these count as being told to move.
CMD_MOVING_THRESHOLD_MPS = 0.05
CMD_MOVING_THRESHOLD_RADPS = 0.05

#: A command older than this no longer says anything: the sender stopped.
_CMD_EXPIRY_SECONDS = 2.0
#: How long a moving robot's pose may stay silent. Long enough for a slow
#: robot to cover a particle filter's update distance (AMCL's `update_min_d`).
SILENT_WHILE_MOVING_SECONDS = 10.0


def commands_motion(linear: float, angular: float) -> bool:
    """True when this velocity command tells the robot to actually move."""
    return abs(linear) > CMD_MOVING_THRESHOLD_MPS or abs(angular) > CMD_MOVING_THRESHOLD_RADPS


class CommandedMotion:
    """Tracks since when the robot has been commanded to move without interruption."""

    def __init__(self) -> None:
        self._moving_since: float | None = None
        self._last_command: float | None = None

    def on_command(self, linear: float, angular: float, now: float) -> None:
        """Take one velocity command received at `now`."""
        expired = self._last_command is None or now - self._last_command > _CMD_EXPIRY_SECONDS
        self._last_command = now
        if not commands_motion(linear, angular):
            self._moving_since = None
        elif self._moving_since is None or expired:
            self._moving_since = now

    def silent_too_long(self, last_output: float | None, now: float) -> bool:
        """True when the robot has been moving, with no output since `last_output`, for too long."""
        if self._moving_since is None or self._last_command is None or now - self._last_command > _CMD_EXPIRY_SECONDS:
            return False
        quiet_since = max(self._moving_since, last_output) if last_output is not None else self._moving_since
        return now - quiet_since >= SILENT_WHILE_MOVING_SECONDS
