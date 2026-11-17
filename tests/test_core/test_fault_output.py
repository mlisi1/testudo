"""The faults topic's DiagnosticStatus layout: synthetic entries, no ROS graph."""
from __future__ import annotations

from testudo.core.fault_history import FaultEntry
from testudo.core.fault_output import FAULTS_SCHEMA_VERSION, build_faults_array, fault_status
from testudo.plugins.base import Severity


def _entry(active: bool) -> FaultEntry:
    return FaultEntry(
        topic="/gps/filtered",
        code="GNSS-002",
        active=active,
        severity=Severity.ERROR,
        message="[red]position σ high[/red]",
        raised_at=1763394767.25,
        cleared_at=None if active else 1763394779.65,
        duration_s=12.4,
        times_raised=3,
    )


def test_an_active_fault_has_its_severity_as_level_and_plottable_values() -> None:
    status = fault_status(_entry(active=True))
    values = {kv.key: kv.value for kv in status.values}
    assert status.name == "testudo: /gps/filtered GNSS-002"
    assert status.level == bytes([Severity.ERROR])
    assert status.message == "position σ high"
    assert values == {
        "topic": "/gps/filtered",
        "code": "GNSS-002",
        "active": "1",
        "severity": "2",
        "raised_at_s": "1763394767.250",
        "cleared_at_s": "0",
        "duration_s": "12.4",
        "times_raised": "3",
        "schema_version": str(FAULTS_SCHEMA_VERSION),
    }


def test_a_cleared_fault_is_ok_but_keeps_the_severity_it_reached() -> None:
    status = fault_status(_entry(active=False))
    values = {kv.key: kv.value for kv in status.values}
    assert status.level == bytes([Severity.OK])
    assert (values["active"], values["severity"], values["cleared_at_s"]) == ("0", "2", "1763394779.650")


def test_array_holds_every_entry_and_can_be_empty() -> None:
    assert len(build_faults_array("stamp", [_entry(True), _entry(False)]).status) == 2
    assert build_faults_array("stamp", []).status == []
