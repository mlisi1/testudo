"""Severity aggregation: per-check hysteresis, and cross-topic roll-up.

Two distinct concerns share this module because both are "aggregation" in
the sense the project's architecture notes use the word, but at different
axes: `HysteresisDebouncer` smooths one check's severity *over time*
(milestone M2); `aggregate_reports` rolls up *many* checks into one overall
status at a single point in time (milestone M4, worst/weighted/both).
"""
from __future__ import annotations

from collections import Counter
from typing import Callable

from testudo.core.config import SeverityMode
from testudo.core.topic_report import TopicReport
from testudo.plugins.base import SEVERITY_LABELS, CheckStatus, Severity

DEFAULT_REQUIRED_CONSECUTIVE = 3


class HysteresisDebouncer:
    """Feed raw `CheckStatus` samples in; read back a debounced status.

    The debounced severity only changes once `required_consecutive` samples
    in a row disagree with it. A sample matching the current debounced
    severity always refreshes its message/values immediately (only the
    *severity* is debounced, not the detail).
    """

    def __init__(self, required_consecutive: int = DEFAULT_REQUIRED_CONSECUTIVE) -> None:
        if required_consecutive < 1:
            raise ValueError("required_consecutive must be >= 1")
        self._required_consecutive = required_consecutive
        self._stable: CheckStatus | None = None
        self._candidate_severity: int | None = None
        self._candidate_count = 0

    @property
    def current(self) -> CheckStatus | None:
        """The current debounced status, or None if `update()` has never been called."""
        return self._stable

    def update(self, status: CheckStatus) -> CheckStatus:
        """Feed one new raw sample and return the (possibly still-unchanged) debounced status."""
        if self._stable is None or status.severity == self._stable.severity:
            self._stable = status
            self._candidate_severity = None
            self._candidate_count = 0
            return self._stable

        if status.severity == self._candidate_severity:
            self._candidate_count += 1
        else:
            self._candidate_severity = status.severity
            self._candidate_count = 1

        if self._candidate_count >= self._required_consecutive:
            self._stable = status
            self._candidate_severity = None
            self._candidate_count = 0

        return self._stable


def aggregate_reports(
    reports: list[TopicReport], weight_of: Callable[[str], float], mode: SeverityMode
) -> CheckStatus:
    """Roll up per-topic reports into one overall `CheckStatus`.

    - `worst`: severity = max across all reports -- the actionable go/no-go
      number, and what drives `check`'s exit code in this mode.
    - `weighted`: severity = the weighted-average severity, rounded -- a
      smoothed number more useful for trend/comparison across runs than for
      alerting on any single topic's spike. Drives the exit code in this mode.
    - `both`: severity = worst (still the actionable one), with the
      weighted score included in `values` as a secondary gauge alongside it.

    Weight defaults to 1 for any topic `weight_of` doesn't know about
    (matches the config schema's own per-topic weight default).
    """
    if not reports:
        return CheckStatus(severity=Severity.OK, label="overall", message="no topics checked")

    worst_severity = max(report.status.severity for report in reports)

    total_weight = sum(weight_of(report.topic) for report in reports)
    weighted_score = (
        sum(report.status.severity * weight_of(report.topic) for report in reports) / total_weight
        if total_weight > 0
        else 0.0
    )

    counts = Counter(report.status.severity for report in reports)
    count_summary = ", ".join(f"{SEVERITY_LABELS[severity]}={counts[severity]}" for severity in sorted(counts))
    message = f"{len(reports)} topic(s): {count_summary}"

    if mode == SeverityMode.WEIGHTED:
        severity = _round_severity(weighted_score)
    else:  # worst and both both use worst as the actionable severity
        severity = worst_severity

    values = {"topics_checked": str(len(reports))}
    if mode != SeverityMode.WORST:
        values["weighted_score"] = f"{weighted_score:.3g}"

    return CheckStatus(severity=severity, label="overall", message=message, values=values)


def _round_severity(weighted_score: float) -> int:
    return max(Severity.OK, min(Severity.STALE, round(weighted_score)))
