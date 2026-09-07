"""Data sources feeding the TUI: live (ticking a SubscriptionManager) or
static (a pre-computed replay result). The app polls whichever it's given
on a fixed timer without needing to know which kind it has.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from testudo.core.config import SeverityMode
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


class LiveDataSource:
    """Polls a live SubscriptionManager, advancing plugins' time-based state each poll."""

    def __init__(self, manager: SubscriptionManager, severity_mode: SeverityMode) -> None:
        self._manager = manager
        self._severity_mode = severity_mode

    def poll(self) -> WatchSnapshot:
        self._manager.tick()
        return WatchSnapshot(reports=self._manager.reports(), overall=self._manager.overall_status(self._severity_mode))

    def reset_stats(self) -> None:
        self._manager.reset_stats()

    def is_live(self) -> bool:
        return True


class StaticDataSource:
    """Always returns the same pre-computed snapshot -- a finished replay's result."""

    def __init__(self, reports: list[TopicReport], overall: CheckStatus) -> None:
        self._snapshot = WatchSnapshot(reports=reports, overall=overall)

    def poll(self) -> WatchSnapshot:
        return self._snapshot

    def reset_stats(self) -> None:
        pass  # nothing to reset in a completed, static replay result

    def is_live(self) -> bool:
        return False
