"""Faults that were active and have since cleared, so a problem that came and went isn't lost.

A fault is one (topic, code) pair active on a non-OK report -- the same
rule the TUI's "Needs attention" list uses. `FaultHistory.update` is fed
every poll's reports; a fault that disappears while its topic is still
reported is recorded as cleared. A topic that vanishes from the reports
altogether (excluded, its stack cleared) takes its faults with it
unrecorded: nothing was fixed, it just stopped being watched.

The time base is the caller's: wall-clock seconds live, bag seconds in a
replay. Only differences between two times from the same source are ever
shown, so the two never mix.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from testudo.core.topic_report import TopicReport
from testudo.plugins.base import Severity

#: Cleared faults kept, newest first: enough for a flapping topic not to
#: push every other one out, without growing for the whole session.
DEFAULT_MAX_CLEARED = 200


@dataclass(frozen=True)
class ClearedFault:
    """One fault from when it was raised to when it cleared.

    `severity` is the worst its report reached while the code was active;
    `message` is the code's last message (plugin markup included).
    """

    topic: str
    code: str
    severity: int
    message: str
    raised_at: float
    cleared_at: float

    @property
    def lasted_s(self) -> float:
        return self.cleared_at - self.raised_at


@dataclass
class _ActiveFault:
    raised_at: float
    severity: int
    message: str


class FaultHistory:
    """Tracks active faults across polls and remembers the ones that cleared."""

    def __init__(self, max_cleared: int = DEFAULT_MAX_CLEARED) -> None:
        self._active: dict[tuple[str, str], _ActiveFault] = {}
        self._cleared: deque[ClearedFault] = deque(maxlen=max_cleared)

    def update(self, reports: list[TopicReport], now: float) -> None:
        """Fold in one poll's `reports`, taken at time `now`."""
        present = {report.topic for report in reports}
        current: dict[tuple[str, str], tuple[int, str]] = {}
        for report in reports:
            if report.status.severity <= Severity.OK:
                continue
            for code, message in report.status.codes.items():
                current[(report.topic, code)] = (report.status.severity, message)

        for key in list(self._active):
            if key in current:
                continue
            fault = self._active.pop(key)
            if key[0] in present:
                self._cleared.appendleft(
                    ClearedFault(key[0], key[1], fault.severity, fault.message, fault.raised_at, now)
                )

        for key, (severity, message) in current.items():
            fault = self._active.get(key)
            if fault is None:
                self._active[key] = _ActiveFault(now, severity, message)
            else:
                fault.severity = max(fault.severity, severity)
                fault.message = message

    def cleared(self) -> tuple[ClearedFault, ...]:
        """Every remembered cleared fault, newest first."""
        return tuple(self._cleared)

    def reset(self) -> None:
        """Forget everything, active faults included (they're re-raised on the next update)."""
        self._active.clear()
        self._cleared.clear()


def latest_per_code(faults: tuple[ClearedFault, ...] | list[ClearedFault]) -> list[tuple[ClearedFault, int]]:
    """(most recent fault, times cleared) per code, newest first, from newest-first `faults`.

    A flapping code clears again and again; one line with a count says
    that without crowding out every other code.
    """
    latest: dict[str, ClearedFault] = {}
    counts: dict[str, int] = {}
    for fault in faults:
        latest.setdefault(fault.code, fault)
        counts[fault.code] = counts.get(fault.code, 0) + 1
    return [(fault, counts[code]) for code, fault in latest.items()]


def format_ago(seconds: float) -> str:
    """A compact age: `12s`, `4m`, `2h` -- one unit, the precision a glance needs."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h"
