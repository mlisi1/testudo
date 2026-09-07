"""Unit tests for TopicVitals rolling stats -- synthetic timestamps, no live subscription."""
from __future__ import annotations

import pytest

from testudo.core.vitals import TopicVitals


def test_fresh_vitals_have_no_arrival_or_rate() -> None:
    vitals = TopicVitals(topic="/odom")
    assert vitals.message_count == 0
    assert vitals.last_arrival_seconds is None
    assert vitals.age_seconds(now_seconds=10.0) is None
    assert vitals.rate_hz() is None
    assert vitals.mean_period_seconds() is None


def test_single_arrival_has_no_rate_but_has_age() -> None:
    vitals = TopicVitals(topic="/odom")
    vitals.record_arrival(now_seconds=10.0)
    assert vitals.message_count == 1
    assert vitals.last_arrival_seconds == 10.0
    assert vitals.age_seconds(now_seconds=12.0) == 2.0
    assert vitals.rate_hz() is None


def test_steady_10hz_arrivals_compute_correct_rate() -> None:
    vitals = TopicVitals(topic="/scan")
    for i in range(10):
        vitals.record_arrival(now_seconds=i * 0.1)
    assert vitals.message_count == 10
    assert vitals.rate_hz() == pytest.approx(10.0)
    assert vitals.mean_period_seconds() == pytest.approx(0.1)


def test_window_is_bounded_by_window_size() -> None:
    vitals = TopicVitals(topic="/imu", window_size=5)
    for i in range(100):
        vitals.record_arrival(now_seconds=float(i))
    assert vitals.message_count == 100
    # rate_hz only sees the last `window_size` samples: 4 intervals of 1s each.
    assert vitals.rate_hz() == pytest.approx(1.0)


def test_age_seconds_reflects_time_since_last_arrival() -> None:
    vitals = TopicVitals(topic="/battery")
    vitals.record_arrival(now_seconds=5.0)
    vitals.record_arrival(now_seconds=5.5)
    assert vitals.age_seconds(now_seconds=8.0) == pytest.approx(2.5)
