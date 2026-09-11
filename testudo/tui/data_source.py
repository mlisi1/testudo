"""Data sources feeding the TUI: live (ticking a SubscriptionManager) or
static (a pre-computed replay result). The app polls whichever it's given
on a fixed timer without needing to know which kind it has.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from testudo.core.config import ExcludeRule, SeverityMode, write_exclude_rules
from testudo.core.subscription_manager import SubscriptionManager, TopicReport
from testudo.plugins.base import CheckStatus


@dataclass(frozen=True)
class WatchSnapshot:
    """One poll's worth of data: every current report, plus the aggregated overall status."""

    reports: list[TopicReport]
    overall: CheckStatus


class WatchDataSource(Protocol):
    """What the TUI needs from either a live or a replayed data source."""

    def poll(self) -> WatchSnapshot: ...

    def reset_stats(self) -> None: ...

    def is_live(self) -> bool: ...

    def exclude_rules(self) -> list[ExcludeRule]: ...

    def add_exclude_rule(self, rule: ExcludeRule) -> int | None: ...

    def remove_exclude_rule(self, rule: ExcludeRule) -> bool: ...


class LiveDataSource:
    """Polls a live SubscriptionManager, advancing plugins' time-based state each poll."""

    def __init__(
        self, manager: SubscriptionManager, severity_mode: SeverityMode, config_path: str | None = None
    ) -> None:
        self._manager = manager
        self._severity_mode = severity_mode
        # Only used to persist an Options-screen exclude rule back to disk
        # (see `add_exclude_rule`/`remove_exclude_rule`) -- `None` (e.g. in
        # a test harness with no real config file) just skips that write,
        # the in-memory rule still takes effect for the rest of the session.
        self._config_path = config_path

    def poll(self) -> WatchSnapshot:
        self._manager.tick()
        return WatchSnapshot(reports=self._manager.reports(), overall=self._manager.overall_status(self._severity_mode))

    def reset_stats(self) -> None:
        self._manager.reset_stats()

    def is_live(self) -> bool:
        return True

    def exclude_rules(self) -> list[ExcludeRule]:
        return self._manager.exclude_rules()

    def add_exclude_rule(self, rule: ExcludeRule) -> int | None:
        affected = self._manager.add_exclude_rule(rule)
        if affected is not None:
            self._persist()
        return affected

    def remove_exclude_rule(self, rule: ExcludeRule) -> bool:
        removed = self._manager.remove_exclude_rule(rule)
        if removed:
            self._persist()
        return removed

    def _persist(self) -> None:
        if self._config_path is not None:
            write_exclude_rules(self._config_path, self._manager.exclude_rules())


class StaticDataSource:
    """Always returns the same pre-computed snapshot -- a finished replay's result.

    Exclude rules are read-only here: a replay's report is already fully
    computed, so nothing live exists to unsubscribe, and there's no config
    file bound to this data source to persist a change into either.
    `exclude_rules()` just reflects whatever `replay_bag` ran with.
    """

    def __init__(
        self, reports: list[TopicReport], overall: CheckStatus, exclude_rules: list[ExcludeRule] | None = None
    ) -> None:
        self._snapshot = WatchSnapshot(reports=reports, overall=overall)
        self._exclude_rules = list(exclude_rules) if exclude_rules is not None else []

    def poll(self) -> WatchSnapshot:
        return self._snapshot

    def reset_stats(self) -> None:
        pass  # nothing to reset in a completed, static replay result

    def is_live(self) -> bool:
        return False

    def exclude_rules(self) -> list[ExcludeRule]:
        return self._exclude_rules

    def add_exclude_rule(self, rule: ExcludeRule) -> int | None:
        return None

    def remove_exclude_rule(self, rule: ExcludeRule) -> bool:
        return False
