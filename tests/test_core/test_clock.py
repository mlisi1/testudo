"""Unit tests for TestudoClock, using a synthetic clock -- no live node needed."""
from __future__ import annotations

from testudo.core.clock import TestudoClock


class _FakeTime:
    def __init__(self, nanoseconds: int) -> None:
        self.nanoseconds = nanoseconds


class _FakeClock:
    def __init__(self, nanoseconds: int) -> None:
        self._nanoseconds = nanoseconds

    def now(self) -> _FakeTime:
        return _FakeTime(self._nanoseconds)


def test_now_seconds_converts_from_nanoseconds() -> None:
    clock = TestudoClock(_FakeClock(2_500_000_000), sim_time_query=lambda: False)
    assert clock.now_seconds() == 2.5


def test_is_sim_time_active_reflects_query() -> None:
    active = TestudoClock(_FakeClock(0), sim_time_query=lambda: True)
    inactive = TestudoClock(_FakeClock(0), sim_time_query=lambda: False)
    assert active.is_sim_time_active() is True
    assert inactive.is_sim_time_active() is False
