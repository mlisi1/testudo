"""Nav2 action goal-lifecycle plugin.

Tracks one action server's goal lifecycle via its status topic
(action_msgs/msg/GoalStatusArray -- common to every ROS 2 action regardless
of the specific action type): accepted/executing/canceling/succeeded/
aborted/canceled counts, success rate, mean time-to-completion, and
invocation frequency. The same mechanism serves both top-level navigation
goals (e.g. navigate_to_pose) and Nav2's recovery behaviors (e.g. spin,
backup, wait) -- whichever actions are declared or auto-discovered.

A goal's *request* (before the server accepts or rejects it) happens over a
service call, not a topic, so it isn't observable here; tracking starts at
acceptance, which is the first point a goal appears in the status array.
"""
from __future__ import annotations

from collections import deque
from typing import Any

from action_msgs.msg import GoalStatus

from testudo.plugins.base import CheckPlugin, CheckStatus, Severity, ThresholdZone, evaluate_zone

_TERMINAL_STATUSES = frozenset({GoalStatus.STATUS_SUCCEEDED, GoalStatus.STATUS_CANCELED, GoalStatus.STATUS_ABORTED})
_ACTIVE_STATUSES = frozenset({GoalStatus.STATUS_ACCEPTED, GoalStatus.STATUS_EXECUTING, GoalStatus.STATUS_CANCELING})

_DURATION_WINDOW_SIZE = 20
_ACCEPTANCE_WINDOW_SIZE = 20


class _GoalRecord:
    __slots__ = ("accepted_stamp", "last_status")

    def __init__(self, accepted_stamp: float, last_status: int) -> None:
        self.accepted_stamp = accepted_stamp
        self.last_status = last_status


class Nav2GoalPlugin(CheckPlugin):
    """Goal-lifecycle, success-rate, duration, and invocation-frequency tracking for one action."""

    def __init__(
        self,
        thresholds: dict[str, ThresholdZone] | None = None,
        related_topics: dict[str, str] | None = None,
    ) -> None:
        super().__init__(thresholds, related_topics)
        self._now = 0.0
        self._message_count = 0
        self._goals: dict[bytes, _GoalRecord] = {}
        self._succeeded = 0
        self._aborted = 0
        self._canceled = 0
        self._active_count = 0
        self._durations: deque[float] = deque(maxlen=_DURATION_WINDOW_SIZE)
        self._acceptance_times: deque[float] = deque(maxlen=_ACCEPTANCE_WINDOW_SIZE)

    @classmethod
    def msg_types(cls) -> tuple[str, ...]:
        return ("action_msgs/msg/GoalStatusArray",)

    @classmethod
    def default_thresholds(cls) -> dict[str, ThresholdZone]:
        # avg_duration_s and frequency_per_min vary too much by action (a
        # navigate_to_pose goal legitimately takes far longer than a spin
        # recovery) to have a sane universal default; success_rate doesn't.
        return {"success_rate": ThresholdZone(green=0.95, orange=0.8)}

    def on_tick(self, now_seconds: float) -> None:
        self._now = now_seconds

    def on_message(self, topic: str, msg: Any) -> None:
        self._message_count += 1
        seen_ids = set()

        for status in msg.status_list:
            goal_id = bytes(status.goal_info.goal_id.uuid)
            seen_ids.add(goal_id)
            accepted_stamp = status.goal_info.stamp.sec + status.goal_info.stamp.nanosec * 1e-9

            record = self._goals.get(goal_id)
            if record is None:
                record = _GoalRecord(accepted_stamp=accepted_stamp, last_status=GoalStatus.STATUS_UNKNOWN)
                self._goals[goal_id] = record
                self._acceptance_times.append(self._now)

            newly_terminal = status.status in _TERMINAL_STATUSES and record.last_status not in _TERMINAL_STATUSES
            record.last_status = status.status

            if newly_terminal:
                self._durations.append(max(0.0, self._now - record.accepted_stamp))
                if status.status == GoalStatus.STATUS_SUCCEEDED:
                    self._succeeded += 1
                elif status.status == GoalStatus.STATUS_ABORTED:
                    self._aborted += 1
                elif status.status == GoalStatus.STATUS_CANCELED:
                    self._canceled += 1

        # The server stops reporting a goal once it's garbage-collected it; forget it too.
        for goal_id in list(self._goals):
            if goal_id not in seen_ids:
                del self._goals[goal_id]

        self._active_count = sum(1 for goal_id in seen_ids if self._goals[goal_id].last_status in _ACTIVE_STATUSES)

    def _terminal_count(self) -> int:
        return self._succeeded + self._aborted + self._canceled

    def _success_rate(self) -> float | None:
        terminal = self._terminal_count()
        return None if terminal == 0 else self._succeeded / terminal

    def _mean_duration(self) -> float | None:
        return None if not self._durations else sum(self._durations) / len(self._durations)

    def _frequency_per_min(self) -> float | None:
        if len(self._acceptance_times) < 2:
            return None
        span = self._acceptance_times[-1] - self._acceptance_times[0]
        if span <= 0:
            return None
        return (len(self._acceptance_times) - 1) / span * 60.0

    def get_status(self) -> CheckStatus:
        if self._message_count == 0:
            return CheckStatus(severity=Severity.OK, label="nav2-action", message="no goal status received yet")

        success_rate = self._success_rate()
        mean_duration = self._mean_duration()
        frequency = self._frequency_per_min()

        success_severity = (
            Severity.OK
            if success_rate is None
            else evaluate_zone(success_rate, self.thresholds.get("success_rate"), higher_is_worse=False)
        )
        duration_severity = (
            Severity.OK if mean_duration is None else evaluate_zone(mean_duration, self.thresholds.get("avg_duration_s"))
        )
        frequency_severity = (
            Severity.OK if frequency is None else evaluate_zone(frequency, self.thresholds.get("frequency_per_min"))
        )
        worst = max(success_severity, duration_severity, frequency_severity)

        problems = []
        if success_severity != Severity.OK:
            problems.append(f"success rate {success_rate:.0%}")
        if duration_severity != Severity.OK:
            problems.append(f"mean duration {mean_duration:.1f}s")
        if frequency_severity != Severity.OK:
            problems.append(f"invocation frequency {frequency:.1f}/min")
        message = "; ".join(problems) if problems else "nominal"

        values = {
            "active_count": str(self._active_count),
            "succeeded": str(self._succeeded),
            "aborted": str(self._aborted),
            "canceled": str(self._canceled),
            "success_rate": f"{success_rate:.3g}" if success_rate is not None else "n/a",
            "mean_duration_s": f"{mean_duration:.3g}" if mean_duration is not None else "n/a",
            "frequency_per_min": f"{frequency:.3g}" if frequency is not None else "n/a",
        }
        return CheckStatus(severity=worst, label="nav2-action", message=message, values=values)
