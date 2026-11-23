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


def test_a_fast_topic_keeps_a_second_of_arrivals_so_jitter_averages_out() -> None:
    # 100 Hz delivered in bursts of five every 50 ms (a busy publisher or scheduler):
    # over 20 arrivals that read as up to ~12% fast on the first real robot.
    vitals = TopicVitals(topic="/imu/mag")
    for burst in range(60):
        for i in range(5):
            vitals.record_arrival(burst * 0.05 + i * 0.0005)
    rate = vitals.rate_hz()
    assert rate is not None and abs(rate - 100.0) / 100.0 < 0.03


def test_a_slow_topic_still_keeps_its_last_twenty_arrivals() -> None:
    vitals = TopicVitals(topic="/slow")
    for i in range(50):
        vitals.record_arrival(i * 0.5)  # 2 Hz: 20 arrivals span 9.5 s
    assert vitals.rate_hz() == pytest.approx(2.0)
    assert len(vitals._arrival_times) == 20
