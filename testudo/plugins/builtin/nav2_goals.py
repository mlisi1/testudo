"""Nav2 action goal-lifecycle plugin.

Tracks one action server's goal lifecycle via its status topic
(action_msgs/msg/GoalStatusArray -- common to every ROS 2 action regardless
of the specific action type): accepted/executing/canceling/succeeded/
aborted/canceled counts, success rate, mean time-to-completion, invocation
frequency, and the current status + time spent in it. The same mechanism
serves both top-level navigation goals (e.g. navigate_to_pose) and Nav2's
recovery behaviors (e.g. spin, backup, wait) -- whichever actions are
declared or auto-discovered.

A goal's *request* (before the server accepts or rejects it) happens over a
service call, not a topic, so it isn't observable here; tracking starts at
acceptance, which is the first point a goal appears in the status array.

Optionally, declaring this action's feedback topic under the action's
`related_topics: {feedback: ...}` (config.py's ActionConfig, routed the
same way a declared topic's related_topics are) feeds remaining-distance/
time-to-completion into the status too, on a best-effort basis: the
feedback message type is action-specific (unlike GoalStatusArray), so
fields are read by duck-typing rather than importing every action's own
Feedback type -- an action whose feedback doesn't carry
`distance_remaining`/`estimated_time_remaining` (e.g. Spin, BackUp, Wait)
simply never populates those two values.
"""
from __future__ import annotations

from collections import deque
from typing import Any

from action_msgs.msg import GoalStatus

from testudo.plugins.base import CheckPlugin, CheckStatus, Severity, ThresholdZone, colorize, evaluate_zone
from testudo.plugins.render import meter, sparkline, strip

_TERMINAL_STATUSES = frozenset({GoalStatus.STATUS_SUCCEEDED, GoalStatus.STATUS_CANCELED, GoalStatus.STATUS_ABORTED})
_ACTIVE_STATUSES = frozenset({GoalStatus.STATUS_ACCEPTED, GoalStatus.STATUS_EXECUTING, GoalStatus.STATUS_CANCELING})

_STATUS_NAMES: dict[int, str] = {
    GoalStatus.STATUS_UNKNOWN: "unknown",
    GoalStatus.STATUS_ACCEPTED: "accepted",
    GoalStatus.STATUS_EXECUTING: "executing",
    GoalStatus.STATUS_CANCELING: "canceling",
    GoalStatus.STATUS_SUCCEEDED: "succeeded",
    GoalStatus.STATUS_CANCELED: "canceled",
    GoalStatus.STATUS_ABORTED: "aborted",
}

_DURATION_WINDOW_SIZE = 20
_ACCEPTANCE_WINDOW_SIZE = 20
_OUTCOME_WINDOW_SIZE = 20
#: Outcomes shown in the Topic Panel's "Last 5" column.
_TOPIC_PANEL_OUTCOMES = 5
_METER_SEGMENTS = 6

#: A terminal status as a `render.strip` outcome: success, failure, or
#: neither (a cancel is a decision, not a failure).
_OUTCOME_SEVERITIES: dict[int, int | None] = {
    GoalStatus.STATUS_SUCCEEDED: Severity.OK,
    GoalStatus.STATUS_ABORTED: Severity.ERROR,
    GoalStatus.STATUS_CANCELED: None,
}

#: The `state` headline's chip for each active status. The Topic Panel's
#: "State" column keys off the leading glyph (see `topic_panel.rate_cell`).
_ACTIVE_CHIPS = {
    GoalStatus.STATUS_ACCEPTED: "[green]▶ ACCEPTED[/green]",
    GoalStatus.STATUS_EXECUTING: "[green]▶ EXECUTING[/green]",
    GoalStatus.STATUS_CANCELING: "[yellow]⊘ CANCELING[/yellow]",
}
_IDLE_CHIP = "[dim]◌ idle[/dim]"


def _failure_zone(success_zone: ThresholdZone | None) -> ThresholdZone | None:
    """A higher-is-better success-rate zone as the equivalent higher-is-worse failure-rate zone (for `meter`)."""
    if success_zone is None:
        return None
    return ThresholdZone(
        *(1.0 - boundary if boundary is not None else None for boundary in (success_zone.green, success_zone.orange, success_zone.red))
    )


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
        self._current_status = GoalStatus.STATUS_UNKNOWN
        self._status_since: float | None = None
        self._distance_remaining_m: float | None = None
        self._estimated_time_remaining_s: float | None = None
        # Each finished goal as (outcome severity or None, duration), newest last.
        self._outcomes: deque[tuple[int | None, float]] = deque(maxlen=_OUTCOME_WINDOW_SIZE)

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
        if topic == self.related_topics.get("feedback"):
            self._handle_feedback(msg)
            return

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

            if status.status != record.last_status:
                self._current_status = status.status
                self._status_since = self._now

            newly_terminal = status.status in _TERMINAL_STATUSES and record.last_status not in _TERMINAL_STATUSES
            record.last_status = status.status

            if newly_terminal:
                duration = max(0.0, self._now - record.accepted_stamp)
                self._durations.append(duration)
                self._outcomes.append((_OUTCOME_SEVERITIES[status.status], duration))
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

    def _handle_feedback(self, msg: Any) -> None:
        """Best-effort extraction of remaining distance/time from an action-specific Feedback message.

        `msg` is the action's `<Action>_FeedbackMessage` wrapper
        (goal_id + feedback); read by attribute name rather than importing
        every action's Feedback type, since only some (NavigateToPose,
        ComputePathToPose, ...) carry these fields at all.
        """
        feedback = getattr(msg, "feedback", None)
        if feedback is None:
            return
        distance = getattr(feedback, "distance_remaining", None)
        if distance is not None:
            self._distance_remaining_m = float(distance)
        duration = getattr(feedback, "estimated_time_remaining", None)
        if duration is not None:
            self._estimated_time_remaining_s = duration.sec + duration.nanosec * 1e-9

    def _success_rate(self) -> float | None:
        """Succeeded out of succeeded + aborted. A cancel is someone's decision (often a
        new goal preempting the old one), not a failure, so it counts toward neither."""
        decided = self._succeeded + self._aborted
        return None if decided == 0 else self._succeeded / decided

    def _mean_duration(self) -> float | None:
        return None if not self._durations else sum(self._durations) / len(self._durations)

    def _frequency_per_min(self) -> float | None:
        if len(self._acceptance_times) < 2:
            return None
        span = self._acceptance_times[-1] - self._acceptance_times[0]
        if span <= 0:
            return None
        return (len(self._acceptance_times) - 1) / span * 60.0

    def _state_line(self) -> str:
        """The headline: what the server is doing now and for how long, and how the last goal ended."""
        time_in_status = None if self._status_since is None else max(0.0, self._now - self._status_since)
        since = f" {time_in_status:.1f}s" if time_in_status is not None else ""
        chip = _ACTIVE_CHIPS.get(self._current_status)
        if chip is not None:
            parts = [f"{chip}{since}"]
            if self._active_count > 1:
                parts.append(f"{self._active_count} goals")
            if self._distance_remaining_m is not None:
                parts.append(f"{self._distance_remaining_m:.1f} m left")
            if self._estimated_time_remaining_s is not None:
                parts.append(f"ETA {self._estimated_time_remaining_s:.0f} s")
            return " · ".join(parts)
        if not self._outcomes:
            return f"{_IDLE_CHIP} · no goal finished yet"
        outcome, duration = self._outcomes[-1]
        ended = {Severity.OK: "succeeded", Severity.ERROR: "aborted"}.get(outcome, "canceled")
        return f"{_IDLE_CHIP}{since} · last {strip([outcome])} {ended} in {duration:.1f}s"

    def _history_line(self) -> str:
        """The recent outcomes as a strip, plus the all-time tallies."""
        tallies = (
            f"{colorize(f'{self._succeeded}✓', Severity.OK)} "
            f"{colorize(f'{self._aborted}✗', Severity.ERROR)} "
            f"[dim]{self._canceled}⊘[/dim]"
        )
        recent = strip(outcome for outcome, _duration in self._outcomes)
        return f"{recent}  {tallies}" if recent else tallies

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
        codes: dict[str, str] = {}
        if success_severity != Severity.OK:
            problems.append(f"success rate {success_rate:.0%}")
            codes["NAV-001"] = colorize(f"success rate {success_rate:.0%}", success_severity)
        if duration_severity != Severity.OK:
            problems.append(f"mean duration {mean_duration:.1f}s")
            codes["NAV-002"] = colorize(f"mean goal duration {mean_duration:.1f}s", duration_severity)
        if frequency_severity != Severity.OK:
            problems.append(f"invocation frequency {frequency:.1f}/min")
            codes["NAV-003"] = colorize(f"invoked {frequency:.1f} times/min", frequency_severity)
        message = "; ".join(problems) if problems else "nominal"

        values = {"state": self._state_line(), "history": self._history_line()}
        # Readings that don't exist yet are left out rather than shown as
        # "n/a": on a short Detail Panel every row counts.
        if success_rate is not None:
            bar = meter(1.0 - success_rate, _failure_zone(self.thresholds.get("success_rate")), _METER_SEGMENTS)
            values["success"] = f"{colorize(f'{success_rate:.0%}', success_severity)} {bar}".rstrip()
        if mean_duration is not None:
            values["mean_duration_s"] = f"{mean_duration:.3g}"
        if frequency is not None:
            values["goal_rate_per_min"] = f"{frequency:.3g}"
        if len(self._durations) > 1:
            values["durations_more"] = f"{sparkline(self._durations, duration_severity)} [dim](last {len(self._durations)})[/dim]"

        recent = strip((outcome for outcome, _duration in self._outcomes), width=_TOPIC_PANEL_OUTCOMES)
        return CheckStatus(
            severity=worst,
            label="nav2-action",
            message=message,
            values=values,
            topic_panel_column="Last 5",
            topic_panel_value=recent or "[dim]–[/dim]",
            codes=codes,
        )
