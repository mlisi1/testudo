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

from testudo.core.config import FaultRetention
from testudo.core.topic_report import TopicReport
from testudo.plugins.base import Severity, code_severity

#: Cleared faults kept, newest first: enough for a flapping topic not to
#: push every other one out, without growing for the whole session.
DEFAULT_MAX_CLEARED = 200


@dataclass(frozen=True)
class ClearedFault:
    """One fault from when it was raised to when it cleared.

    `severity` is the worst the code itself reached while active (`code_severity`);
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


@dataclass(frozen=True)
class FaultEntry:
    """One (topic, code) fault as published: active now, or its latest occurrence once cleared.

    `cleared_at` is None while active; `duration_s` is how long it has been
    (or was) active; `times_raised` counts its occurrences this session
    (under `recent` retention, since it last aged out).
    """

    topic: str
    code: str
    active: bool
    severity: int
    message: str
    raised_at: float
    cleared_at: float | None
    duration_s: float
    times_raised: int


class FaultHistory:
    """Tracks active faults across polls and remembers the ones that cleared.

    `retention` (see `FaultRetention`) decides which cleared faults stay:
    `session` keeps up to `max_cleared` of them, `recent` additionally
    drops each `recent_window_seconds` after it cleared. Active faults
    always stay.
    """

    def __init__(
        self,
        max_cleared: int = DEFAULT_MAX_CLEARED,
        retention: FaultRetention = FaultRetention.SESSION,
        recent_window_seconds: float = 600.0,
    ) -> None:
        self._active: dict[tuple[str, str], _ActiveFault] = {}
        self._cleared: deque[ClearedFault] = deque(maxlen=max_cleared)
        self._times_raised: dict[tuple[str, str], int] = {}
        self._retention = retention
        self._recent_window_seconds = recent_window_seconds

    def update(self, reports: list[TopicReport], now: float) -> None:
        """Fold in one poll's `reports`, taken at time `now`."""
        present = {report.topic for report in reports}
        current: dict[tuple[str, str], tuple[int, str]] = {}
        for report in reports:
            if report.status.severity <= Severity.OK:
                continue
            for code, message in report.status.codes.items():
                current[(report.topic, code)] = (code_severity(report.status, code), message)

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
                self._times_raised[key] = self._times_raised.get(key, 0) + 1
            else:
                fault.severity = max(fault.severity, severity)
                fault.message = message
        self._prune(now)

    def _prune(self, now: float) -> None:
        if self._retention == FaultRetention.RECENT:
            while self._cleared and now - self._cleared[-1].cleared_at > self._recent_window_seconds:
                self._cleared.pop()
        # A fault neither active nor remembered any more starts counting afresh.
        remembered = set(self._active) | {(fault.topic, fault.code) for fault in self._cleared}
        for key in self._times_raised.keys() - remembered:
            del self._times_raised[key]

    def cleared(self) -> tuple[ClearedFault, ...]:
        """Every remembered cleared fault, newest first."""
        return tuple(self._cleared)

    def entries(self, now: float) -> list[FaultEntry]:
        """One entry per (topic, code): active ones as they stand at `now`, else the latest that cleared.

        Sorted by topic then code, so a viewer lists the same fault in the same place.
        """
        entries: dict[tuple[str, str], FaultEntry] = {}
        for key, fault in self._active.items():
            entries[key] = FaultEntry(
                topic=key[0],
                code=key[1],
                active=True,
                severity=fault.severity,
                message=fault.message,
                raised_at=fault.raised_at,
                cleared_at=None,
                duration_s=now - fault.raised_at,
                times_raised=self._times_raised.get(key, 1),
            )
        for fault in self._cleared:  # newest first: the first seen per key is its latest
            key = (fault.topic, fault.code)
            if key not in entries:
                entries[key] = FaultEntry(
                    topic=fault.topic,
                    code=fault.code,
                    active=False,
                    severity=fault.severity,
                    message=fault.message,
                    raised_at=fault.raised_at,
                    cleared_at=fault.cleared_at,
                    duration_s=fault.lasted_s,
                    times_raised=self._times_raised.get(key, 1),
                )
        return [entries[key] for key in sorted(entries)]

    def reset(self) -> None:
        """Forget everything, active faults included (they're re-raised on the next update)."""
        self._active.clear()
        self._cleared.clear()
        self._times_raised.clear()


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
