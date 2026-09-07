"""Unit tests for HysteresisDebouncer -- synthetic CheckStatus samples, no ROS needed."""
from __future__ import annotations

import pytest

from testudo.core.aggregator import HysteresisDebouncer
from testudo.plugins.base import CheckStatus, Severity


def _status(severity: int, message: str = "x") -> CheckStatus:
    return CheckStatus(severity=severity, label="test", message=message)


def test_first_sample_is_accepted_immediately() -> None:
    debouncer = HysteresisDebouncer(required_consecutive=3)
    assert debouncer.current is None
    result = debouncer.update(_status(Severity.WARN))
    assert result.severity == Severity.WARN
    assert debouncer.current is result


def test_matching_severity_refreshes_detail_without_debounce() -> None:
    debouncer = HysteresisDebouncer(required_consecutive=3)
    debouncer.update(_status(Severity.OK, "first"))
    result = debouncer.update(_status(Severity.OK, "second"))
    assert result.severity == Severity.OK
    assert result.message == "second"


def test_severity_change_requires_consecutive_agreement() -> None:
    debouncer = HysteresisDebouncer(required_consecutive=3)
    debouncer.update(_status(Severity.OK))

    assert debouncer.update(_status(Severity.ERROR)).severity == Severity.OK
    assert debouncer.update(_status(Severity.ERROR)).severity == Severity.OK
    result = debouncer.update(_status(Severity.ERROR))
    assert result.severity == Severity.ERROR


def test_flapping_candidate_resets_the_count() -> None:
    debouncer = HysteresisDebouncer(required_consecutive=3)
    debouncer.update(_status(Severity.OK))

    debouncer.update(_status(Severity.ERROR))  # candidate=ERROR, count=1
    debouncer.update(_status(Severity.WARN))  # candidate resets to WARN, count=1
    result = debouncer.update(_status(Severity.ERROR))  # candidate resets to ERROR again, count=1
    assert result.severity == Severity.OK  # still hasn't reached 3 consecutive agreements


def test_returning_to_stable_severity_cancels_the_candidate() -> None:
    debouncer = HysteresisDebouncer(required_consecutive=3)
    debouncer.update(_status(Severity.OK))
    debouncer.update(_status(Severity.ERROR))
    debouncer.update(_status(Severity.OK))  # back to stable severity -- cancels the ERROR candidate

    debouncer.update(_status(Severity.ERROR))
    result = debouncer.update(_status(Severity.ERROR))
    assert result.severity == Severity.OK  # only 2 consecutive ERRORs since the reset


def test_required_consecutive_of_one_transitions_immediately() -> None:
    debouncer = HysteresisDebouncer(required_consecutive=1)
    debouncer.update(_status(Severity.OK))
    result = debouncer.update(_status(Severity.ERROR))
    assert result.severity == Severity.ERROR


def test_rejects_non_positive_required_consecutive() -> None:
    with pytest.raises(ValueError):
        HysteresisDebouncer(required_consecutive=0)
