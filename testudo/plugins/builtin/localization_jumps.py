"""Pose jump bookkeeping, shared by the Localization plugin and the `map → odom` row.

A jump is a pose change the motion since the previous sample can't explain.
It's an instant, so a tracker holds on to it for a while: long enough for
hysteresis to confirm it and for someone to read it.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Any

from testudo.plugins.base import Severity, SeverityLevels, evaluate_levels
from testudo.plugins.builtin.localization_roles import CONTINUOUS_ROLE, GLOBAL_ROLE

#: How long a jump stays an active code after it happened.
JUMP_HOLD_SECONDS = 10.0
#: The window relocalizations are counted over.
RELOCALIZATION_WINDOW_SECONDS = 60.0

#: A correction this large on a global topic counts as a relocalization.
_RELOCALIZATION_M = 0.5

#: Margin on the reported speed: it's sampled, and the robot may be accelerating.
_SPEED_MARGIN = 1.5
#: Sensor noise a stationary pose may show between two samples.
_NOISE_M = 0.01
_NOISE_RAD = 0.01
#: With no velocity on the message, the most a robot is credited with moving,
#: over at most `_MAX_CREDITED_GAP_S`: a topic silent for a minute hasn't
#: earned the right to teleport.
_PLAUSIBLE_SPEED_MPS = 3.0
_PLAUSIBLE_YAW_RATE_RADPS = 3.0
_MAX_CREDITED_GAP_S = 2.0


def quaternion_yaw(orientation: Any) -> float:
    """The yaw of a geometry_msgs Quaternion, in radians."""
    x, y, z, w = orientation.x, orientation.y, orientation.z, orientation.w
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def motion_allowance(dt: float, speeds: tuple[float, float] | None) -> tuple[float, float]:
    """How far (meters, radians) a robot may legitimately move in `dt` seconds.

    `speeds` is the reported (linear, angular) speed, or None when the
    message carries no velocity.
    """
    if speeds is None:
        gap = min(dt, _MAX_CREDITED_GAP_S)
        return _PLAUSIBLE_SPEED_MPS * gap, _PLAUSIBLE_YAW_RATE_RADPS * gap
    linear, angular = speeds
    return linear * _SPEED_MARGIN * dt + _NOISE_M, angular * _SPEED_MARGIN * dt + _NOISE_RAD


@dataclass(frozen=True)
class Jump:
    """One detected jump: when, how far beyond the plausible motion, and its severity."""

    at_seconds: float
    distance_m: float
    yaw_rad: float
    severity: int


class JumpTracker:
    """Follows one pose stream: what moved since the last sample, and the jumps found in it."""

    def __init__(self) -> None:
        self._last_pose: tuple[tuple[float, float, float], float] | None = None
        self._held: Jump | None = None
        self._relocalizations: deque[float] = deque()
        self.count = 0
        self.last: Jump | None = None

    def restart(self) -> None:
        """Forget the last pose (the frame changed): the next sample has nothing to compare against."""
        self._last_pose = None

    def step(
        self, position: tuple[float, float, float], yaw: float, allowed_m: float = 0.0, allowed_rad: float = 0.0
    ) -> tuple[float, float]:
        """Take a new sample; how far (meters, radians) it moved beyond what's allowed."""
        previous, self._last_pose = self._last_pose, (position, yaw)
        if previous is None:
            return 0.0, 0.0
        moved = math.dist(previous[0], position)
        turned = abs(math.remainder(yaw - previous[1], math.tau))
        return max(0.0, moved - allowed_m), max(0.0, turned - allowed_rad)

    def record(self, jump: Jump) -> None:
        """Count `jump`, holding it unless a worse one is still being held."""
        self.count += 1
        self.last = jump
        held = self.held(jump.at_seconds)
        if held is None or jump.severity >= held.severity:
            self._held = jump

    def held(self, now: float) -> Jump | None:
        """The jump to report at `now`, or None once it's been held long enough."""
        if self._held is None or now - self._held.at_seconds >= JUMP_HOLD_SECONDS:
            return None
        return self._held

    def note_relocalization(self, now: float) -> None:
        """Count one relocalization towards `relocalizations`."""
        self._relocalizations.append(now)

    def relocalizations(self, now: float) -> int:
        """Relocalizations within the last `RELOCALIZATION_WINDOW_SECONDS`."""
        while self._relocalizations and now - self._relocalizations[0] >= RELOCALIZATION_WINDOW_SECONDS:
            self._relocalizations.popleft()
        return len(self._relocalizations)


class PoseJumpCheck:
    """The Localization plugin's jump checks (LOC-007, LOC-008) on one topic's pose messages."""

    def __init__(self) -> None:
        self._tracker = JumpTracker()
        self._last_stamp: float | None = None
        self._last_speeds: tuple[float, float] = (0.0, 0.0)

    def restart(self) -> None:
        """The frame changed: don't compare the next pose with this one."""
        self._tracker.restart()

    def observe(self, msg: Any, stamp_seconds: float, now: float, role: str | None, levels: dict[str, SeverityLevels]) -> None:
        """Compare this pose with the previous one, crediting the motion the message reports.

        Without two distinct stamps there's no elapsed time to credit, so
        the sample only becomes the next comparison's reference.
        """
        dt = stamp_seconds - self._last_stamp if self._last_stamp is not None else 0.0
        self._last_stamp = stamp_seconds
        pose = msg.pose.pose
        position = (float(pose.position.x), float(pose.position.y), float(pose.position.z))
        allowed = motion_allowance(max(dt, 0.0), self._reported_speeds(msg))
        distance, turned = self._tracker.step(position, quaternion_yaw(pose.orientation), *allowed)
        if dt <= 0:
            return
        prefix = "global_" if role == GLOBAL_ROLE else ""
        severity = max(
            evaluate_levels(distance, levels.get(f"{prefix}pose_jump_m")),
            evaluate_levels(turned, levels.get(f"{prefix}pose_jump_rad")),
        )
        if severity != Severity.OK:
            self._tracker.record(Jump(now, distance, turned, severity))
        if role == GLOBAL_ROLE and distance >= _RELOCALIZATION_M:
            self._tracker.note_relocalization(now)

    def _reported_speeds(self, msg: Any) -> tuple[float, float] | None:
        """The faster of this sample's and the previous one's (linear, angular) speed; None without a twist."""
        if not hasattr(msg, "twist"):
            return None
        linear, angular = msg.twist.twist.linear, msg.twist.twist.angular
        current = (math.hypot(linear.x, linear.y, linear.z), abs(angular.z))
        speeds = (max(current[0], self._last_speeds[0]), max(current[1], self._last_speeds[1]))
        self._last_speeds = current
        return speeds

    def findings(self, now: float, levels: dict[str, SeverityLevels]) -> list[tuple[str, int, str]]:
        """The active (code, severity, message) of this topic's jumps."""
        findings = []
        held = self._tracker.held(now)
        if held is not None:
            findings.append(("LOC-007", held.severity, f"pose jumped {_describe(held)}"))
        relocalizations = self._tracker.relocalizations(now)
        burst = evaluate_levels(relocalizations, levels.get("relocalizations_per_min"))
        if burst != Severity.OK:
            findings.append(("LOC-008", burst, f"{relocalizations} relocalizations in the last minute"))
        return findings

    def summary(self, now: float) -> str | None:
        """The Detail Panel's line on jumps so far, or None before the first."""
        last = self._tracker.last
        if last is None:
            return None
        ago = max(0.0, now - last.at_seconds)
        return f"{self._tracker.count}  [dim]last {_describe(last)}, {ago:.0f} s ago[/dim]"

    def metrics(self, now: float) -> dict[str, float]:
        """The published jump counters."""
        return {"pose_jumps": self._tracker.count, "relocalizations_last_min": self._tracker.relocalizations(now)}


def _describe(jump: Jump) -> str:
    return f"{jump.distance_m:.2f} m, {math.degrees(jump.yaw_rad):.0f}°"
