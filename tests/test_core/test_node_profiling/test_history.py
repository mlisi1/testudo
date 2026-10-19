"""ProcessHistory: trends and memory slope from synthetic readings."""
from __future__ import annotations

import pytest

from testudo.core.node_profiling.history import ProcessHistory


def test_cpu_skips_warm_up_placeholders() -> None:
    history = ProcessHistory()
    history.add(0.0, None, 10.0)
    history.add(2.0, 5.0, 10.0)
    assert history.cpu() == (5.0,)
    assert history.memory() == (10.0, 10.0)


def test_memory_slope_needs_a_long_enough_window() -> None:
    history = ProcessHistory()
    for second in range(0, 30, 2):
        history.add(float(second), 1.0, 100.0 + second)
    assert history.memory_slope_mb_per_min() is None


def test_memory_slope_is_mb_per_minute() -> None:
    history = ProcessHistory()
    for second in range(0, 120, 2):
        history.add(float(second), 1.0, 100.0 + second / 60.0 * 12.0)
    assert history.memory_slope_mb_per_min() == pytest.approx(12.0)


def test_window_keeps_only_the_last_readings() -> None:
    history = ProcessHistory(length=3)
    for value in range(5):
        history.add(float(value), float(value), float(value))
    assert history.cpu() == (2.0, 3.0, 4.0)
