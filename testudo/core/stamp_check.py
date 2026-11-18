"""Header stamp sanity for every full-tier topic, whichever plugin checks it.

A core check, like `rate_hz`: a stamp that is unset, ahead of the clock, or
far behind it breaks TF lookups and message filters downstream, whatever
the message carries. It costs nothing extra: the message is already
deserialized, and only the decimated messages are looked at. The codes join
the plugin's status before hysteresis, so they debounce and can be
overridden (`codes:`) like any plugin's own.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from testudo.core.config import TopicConfig
from testudo.core.topic_report import with_code
from testudo.plugins.base import CheckStatus, Severity, SeverityLevels, evaluate_levels

STAMP_ZERO_CODE = "STAMP-001"
STAMP_FUTURE_CODE = "STAMP-002"
STAMP_LAG_CODE = "STAMP-003"

#: `thresholds:` keys of a declared topic, both in seconds.
STAMP_LAG_METRIC = "stamp_lag_s"
STAMP_FUTURE_METRIC = "stamp_future_s"

#: A slow pipeline is degraded; seconds behind means consumers are working on
#: the past. Generous on purpose: a lidar's stamp is its first ray, and
#: perception nodes stamp their output with their input's time.
DEFAULT_LAG_LEVELS = SeverityLevels(warn=0.5, error=5.0)
#: Small clock skew between machines is normal; a second ahead isn't.
DEFAULT_FUTURE_LEVELS = SeverityLevels(warn=0.1, error=1.0)

#: Further from the clock than this, the stamp isn't late: it's counted from
#: another origin (sim time against wall time, a sensor's uptime).
_OTHER_TIME_BASE_S = 86400.0
_OTHER_TIME_BASE_HINT = ": a different time base (is use_sim_time set?)"


def header_stamp_seconds(msg: Any) -> float | None:
    """`msg.header.stamp` in seconds, or None for a message without one."""
    stamp = getattr(getattr(msg, "header", None), "stamp", None)
    try:
        return stamp.sec + stamp.nanosec * 1e-9
    except AttributeError:
        return None


@dataclass
class StampCheck:
    """One topic's stamp verdict, refreshed by `observe` and added to a status by `applied_to`.

    `judge_other_time_base=False` is for replay: a bag recorded from a
    simulator normally holds sim-time stamps next to wall-clock receive
    times, which says nothing about the stack. Live, the same gap is a real
    finding (a driver stamping with its uptime, Testudo missing use_sim_time).
    """

    lag_levels: SeverityLevels = DEFAULT_LAG_LEVELS
    future_levels: SeverityLevels = DEFAULT_FUTURE_LEVELS
    judge_other_time_base: bool = True
    _lag_s: float | None = field(default=None, init=False)
    _findings: list[tuple[str, int, str]] = field(default_factory=list, init=False)

    def observe(self, msg: Any, now: float, *, latched: bool = False) -> None:
        """Judge `msg`'s stamp against `now`.

        A latched topic is only checked for a stamp from the future: its
        message may be arbitrarily old by the time it's received, and a
        static one is often never stamped at all. `now <= 0` is a sim clock
        that hasn't started, so there's nothing to compare against yet.
        """
        self._lag_s = None
        self._findings = []
        stamp = header_stamp_seconds(msg)
        if stamp is None or now <= 0:
            return
        if stamp == 0:
            if not latched:
                self._findings.append((STAMP_ZERO_CODE, Severity.ERROR, "header stamp is zero"))
            return
        lag = now - stamp
        other_base = abs(lag) > _OTHER_TIME_BASE_S
        if other_base and not self.judge_other_time_base:
            return
        self._lag_s = lag
        hint = _OTHER_TIME_BASE_HINT if other_base else ""
        if lag < 0:
            severity = evaluate_levels(-lag, self.future_levels)
            if severity != Severity.OK:
                self._findings.append((STAMP_FUTURE_CODE, severity, f"stamp {-lag:.3g} s ahead of the clock{hint}"))
        elif not latched:
            severity = evaluate_levels(lag, self.lag_levels)
            if severity != Severity.OK:
                self._findings.append((STAMP_LAG_CODE, severity, f"stamp {lag:.3g} s behind the clock{hint}"))

    def applied_to(self, status: CheckStatus) -> CheckStatus:
        """`status` with the stamp's lag among its values and any stamp problem as an active code."""
        if self._lag_s is not None:
            # `metrics` only if the plugin publishes some: a status without
            # any publishes its `values` instead (`plain_values.published_values`).
            metrics = {**status.metrics, STAMP_LAG_METRIC: self._lag_s} if status.metrics else status.metrics
            status = replace(status, values={**status.values, STAMP_LAG_METRIC: f"{self._lag_s:.3f}"}, metrics=metrics)
        for code, severity, message in self._findings:
            if status.severity == Severity.OK:
                status = replace(status, message=message)
            status = with_code(status, code, severity, message)
        return status


def stamp_check_for(topic_config: TopicConfig | None, *, judge_other_time_base: bool = True) -> StampCheck:
    """A `StampCheck` with a declared topic's `stamp_lag_s`/`stamp_future_s` written over the defaults."""
    levels = topic_config.levels if topic_config is not None else {}
    lag = levels.get(STAMP_LAG_METRIC)
    future = levels.get(STAMP_FUTURE_METRIC)
    return StampCheck(
        lag_levels=lag.over(DEFAULT_LAG_LEVELS) if lag is not None else DEFAULT_LAG_LEVELS,
        future_levels=future.over(DEFAULT_FUTURE_LEVELS) if future is not None else DEFAULT_FUTURE_LEVELS,
        judge_other_time_base=judge_other_time_base,
    )
