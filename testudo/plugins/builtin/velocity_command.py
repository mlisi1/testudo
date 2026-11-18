"""Velocity command content-check plugin for geometry_msgs Twist and TwistStamped.

Judges what a controller asks of the robot: NaN/Inf commands, a robot that
doesn't move when told to (needs an `odom` related topic: which odometry is
authoritative is never guessed), a command reversing direction over and
over, and a turn rate pinned at one value. Cruising at a constant forward
speed is normal, so only the angular axis counts as pinned.

An optional `goal_status` related topic (an action's GoalStatusArray) limits
the stuck and pinned checks to the time a goal is executing, which leaves
teleoperation alone. A command topic is silent whenever the robot is parked.
"""
from __future__ import annotations

import math
from typing import Any

from action_msgs.msg import GoalStatus

from testudo.plugins.base import ActiveCodes, CheckPlugin, CheckStatus, CodeMetric, Severity, SeverityLevels, ThresholdZone, evaluate_levels
from testudo.plugins.builtin.localization_motion import (
    CMD_MOVING_THRESHOLD_MPS,
    CMD_MOVING_THRESHOLD_RADPS,
    commands_motion,
)
from testudo.plugins.builtin.velocity_command_checks import EXPIRY_SECONDS, PinnedAxis, ReversalRate, StuckCheck

_LABEL = "velocity"
#: A bad command stays an active code this long: one is enough to matter.
_INVALID_HOLD_SECONDS = 10.0


class VelocityCommandPlugin(CheckPlugin):
    """Stuck robot, oscillation, pinned turn rate and NaN/Inf checks on a velocity command topic."""

    def __init__(
        self,
        thresholds: dict[str, ThresholdZone] | None = None,
        related_topics: dict[str, str] | None = None,
        *,
        levels: dict[str, SeverityLevels] | None = None,
    ) -> None:
        super().__init__(thresholds, related_topics, levels=levels)
        self._now = 0.0
        self._message_count = 0
        self._linear = 0.0
        self._angular = 0.0
        self._last_command = 0.0
        self._invalid_at: float | None = None
        self._goal_active = False
        self._stuck = StuckCheck()
        self._linear_reversals = ReversalRate(CMD_MOVING_THRESHOLD_MPS)
        self._angular_reversals = ReversalRate(CMD_MOVING_THRESHOLD_RADPS)
        self._pinned = PinnedAxis(CMD_MOVING_THRESHOLD_RADPS)

    @classmethod
    def msg_types(cls) -> tuple[str, ...]:
        return ("geometry_msgs/msg/Twist", "geometry_msgs/msg/TwistStamped")

    @classmethod
    def default_thresholds(cls) -> dict[str, ThresholdZone]:
        return {}

    @classmethod
    def default_levels(cls) -> dict[str, SeverityLevels]:
        return {
            "stuck_seconds": SeverityLevels(warn=3.0, error=10.0),
            "reversals_per_s": SeverityLevels(warn=1.0),
            "pinned_seconds": SeverityLevels(warn=5.0),
        }

    @classmethod
    def needs_every_message(cls) -> bool:
        # Decimating a command that flips every message would hide the flipping.
        return True

    def silence_expected(self, msg_type: str) -> bool:
        return True

    def _judged(self) -> bool:
        """False while a topic that watches goals has none executing."""
        return "goal_status" not in self.related_topics or self._goal_active

    def on_tick(self, now_seconds: float) -> None:
        self._now = now_seconds
        self._stuck.update(now_seconds, self._judged())

    def on_message(self, topic: str, msg: Any) -> None:
        if topic == self.related_topics.get("odom"):
            twist = msg.twist.twist
            self._stuck.on_odometry(math.hypot(twist.linear.x, twist.linear.y), twist.angular.z, self._now)
        elif topic == self.related_topics.get("goal_status"):
            self._goal_active = any(goal.status == GoalStatus.STATUS_EXECUTING for goal in msg.status_list)
        else:
            self._on_command(getattr(msg, "twist", msg))
        self._stuck.update(self._now, self._judged())

    def _on_command(self, twist: Any) -> None:
        self._message_count += 1
        self._last_command = self._now
        fields = (twist.linear.x, twist.linear.y, twist.linear.z, twist.angular.x, twist.angular.y, twist.angular.z)
        if not all(math.isfinite(value) for value in fields):
            self._invalid_at = self._now
            return
        # Signed, so reversing reads as reversing; sideways speed counts for a holonomic base.
        self._linear = math.copysign(math.hypot(twist.linear.x, twist.linear.y), twist.linear.x)
        self._angular = twist.angular.z
        self._stuck.on_command(self._linear, self._angular, self._now)
        self._linear_reversals.observe(twist.linear.x, self._now)
        self._angular_reversals.observe(twist.angular.z, self._now)
        self._pinned.observe(twist.angular.z, self._now)

    def get_status(self) -> CheckStatus:
        if self._message_count == 0:
            return CheckStatus(severity=Severity.OK, label=_LABEL, message="no commands received yet")

        stuck = self._stuck.seconds(self._now)
        reversals = max(self._linear_reversals.per_second(self._now), self._angular_reversals.per_second(self._now))
        pinned = self._pinned.seconds(self._now) if self._judged() else 0.0

        findings: list[tuple[str, int, str]] = []
        if self._invalid_at is not None and self._now - self._invalid_at < _INVALID_HOLD_SECONDS:
            findings.append(("VEL-001", Severity.ERROR, "NaN/Inf in a velocity command"))
        severity = evaluate_levels(stuck, self.levels.get("stuck_seconds"))
        if severity != Severity.OK:
            findings.append(("VEL-002", severity, f"commanded to move for {stuck:.1f} s, odometry reports no motion"))
        severity = evaluate_levels(reversals, self.levels.get("reversals_per_s"))
        if severity != Severity.OK:
            findings.append(("VEL-003", severity, f"command reversing direction {reversals:.1f} times/s"))
        severity = evaluate_levels(pinned, self.levels.get("pinned_seconds"))
        if severity != Severity.OK:
            findings.append(("VEL-004", severity, f"turn rate pinned at {self._pinned.value:.2f} rad/s for {pinned:.1f} s"))
        codes = ActiveCodes()
        for code, severity, text in findings:
            codes.add(code, text, severity)

        command = f"{self._linear:.2f} m/s, {self._angular:+.2f} rad/s"
        moving = commands_motion(self._linear, self._angular)
        if self._now - self._last_command > EXPIRY_SECONDS:
            summary = f"silent, last command {command}" if moving else "silent, last command was to stop"
        else:
            summary = f"commanding {command}" if moving else "stopped"
        return CheckStatus(
            severity=max((severity for _code, severity, _text in findings), default=Severity.OK),
            label=_LABEL,
            message="; ".join(text for _code, _severity, text in findings) or summary,
            values=self._values(stuck, reversals, pinned),
            topic_panel_column="Cmd",
            topic_panel_value=f"{self._linear:.2f} {self._angular:+.2f}",
            codes=codes.messages,
            code_severities=codes.severities,
            code_metrics={"VEL-002": CodeMetric("stuck_seconds", stuck), "VEL-003": CodeMetric("reversals_per_s", reversals), "VEL-004": CodeMetric("pinned_seconds", pinned)},
            metrics=self._metrics(stuck, reversals, pinned),
        )

    def _values(self, stuck: float, reversals: float, pinned: float) -> dict[str, str]:
        """What the Detail Panel shows."""
        values = {
            "linear": f"{self._linear:.2f} m/s",
            "angular": f"{self._angular:+.2f} rad/s",
            "reversals": f"{reversals:.1f} /s",
            "pinned": f"{pinned:.1f} s",
            "not_moving": f"{stuck:.1f} s" if "odom" in self.related_topics else "[dim]needs an odom related topic[/dim]",
        }
        if "goal_status" in self.related_topics:
            values["goal"] = "executing" if self._goal_active else "[dim]none executing[/dim]"
        return values

    def _metrics(self, stuck: float, reversals: float, pinned: float) -> dict[str, float | str]:
        """The published numbers."""
        metrics: dict[str, float | str] = {
            "linear_m_per_s": self._linear,
            "angular_z_rad_per_s": self._angular,
            "reversals_per_s": reversals,
            "pinned_s": pinned,
        }
        if "odom" in self.related_topics:
            metrics["stuck_s"] = stuck
        if "goal_status" in self.related_topics:
            metrics["goal_active"] = self._goal_active
        return metrics
