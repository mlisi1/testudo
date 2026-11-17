"""apply_code_overrides: synthetic statuses, no ROS graph."""
from __future__ import annotations

from testudo.core.code_overrides import apply_code_overrides
from testudo.plugins.base import ActiveCodes, CheckStatus, Severity


def _status(severity: int, *codes: tuple[str, int]) -> CheckStatus:
    active = ActiveCodes()
    for code, code_severity in codes:
        active.add(code, f"{code} message", code_severity)
    return CheckStatus(severity, "x", "", codes=active.messages, code_severities=active.severities)


def test_no_applicable_override_leaves_the_status_untouched() -> None:
    status = _status(Severity.ERROR, ("GNSS-001", Severity.ERROR))
    assert apply_code_overrides(status, {"ODOM-001": Severity.OK}) is status


def test_a_code_lowered_to_warn_is_recolored_and_the_topic_follows() -> None:
    status = apply_code_overrides(_status(Severity.ERROR, ("GNSS-001", Severity.ERROR)), {"GNSS-001": Severity.WARN})
    assert status.severity == Severity.WARN
    assert status.codes == {"GNSS-001": "[yellow]GNSS-001 message[/yellow]"}
    assert status.code_severities == {"GNSS-001": Severity.WARN}


def test_a_code_set_to_ok_is_dropped_and_the_rest_decide() -> None:
    status = _status(Severity.ERROR, ("GNSS-001", Severity.ERROR), ("GNSS-007", Severity.WARN))
    status = apply_code_overrides(status, {"GNSS-001": Severity.OK})
    assert status.severity == Severity.WARN
    assert set(status.codes) == {"GNSS-007"}
    assert apply_code_overrides(status, {"GNSS-007": Severity.OK}).severity == Severity.OK


def test_a_code_can_be_raised_to_error() -> None:
    status = apply_code_overrides(_status(Severity.WARN, ("SENS-005", Severity.WARN)), {"SENS-005": Severity.ERROR})
    assert status.severity == Severity.ERROR


def test_a_stale_topic_follows_its_lowered_live_code() -> None:
    status = apply_code_overrides(_status(Severity.STALE, ("LIVE-003", Severity.STALE)), {"LIVE-003": Severity.WARN})
    assert status.severity == Severity.WARN


def test_a_status_worse_than_its_codes_keeps_its_severity() -> None:
    # A plugin that doesn't code every problem: lowering its one code can't
    # clear what the code doesn't explain.
    status = apply_code_overrides(_status(Severity.ERROR, ("X-001", Severity.WARN)), {"X-001": Severity.OK})
    assert status.severity == Severity.ERROR
    assert status.codes == {}
