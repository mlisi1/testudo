"""Fault history as a DiagnosticArray: one DiagnosticStatus per (topic, code), on `faults.publish_topic`.

Republished every poll, so a plotting tool (PlotJuggler, Foxglove) gets
one series per fault, named `testudo: <topic> <CODE>`, and a bag records
exactly when each was active:

- `level` is the fault's severity right now, OK once it cleared, so
  plotting it shows the active periods directly (the `severity` value is
  the worst it reached);
- `message` is the code's last message, plain text;
- `values` hold the same facts as numbers (`active` 1/0, `severity`,
  `raised_at_s`/`cleared_at_s` as Unix seconds, `duration_s`,
  `times_raised`) plus `topic`/`code` and `schema_version`.

`schema_version` is bumped whenever a key changes meaning or goes away;
adding a key doesn't bump it, so a consumer should ignore keys it doesn't
know rather than reject them.
"""
from __future__ import annotations

from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue

from testudo.core.fault_history import FaultEntry
from testudo.core.plain_values import format_metric, strip_markup
from testudo.core.publisher import NAME_PREFIX

#: The faults topic's key layout (see the module docstring).
FAULTS_SCHEMA_VERSION = 1


def fault_status(entry: FaultEntry) -> DiagnosticStatus:
    """One fault as a DiagnosticStatus (see the module docstring for its layout)."""
    status = DiagnosticStatus()
    status.name = f"{NAME_PREFIX}: {entry.topic} {entry.code}"
    status.level = bytes([entry.current_severity])
    status.message = strip_markup(entry.message)
    status.hardware_id = NAME_PREFIX
    values: dict[str, str | float] = {
        "topic": entry.topic,
        "code": entry.code,
        "active": 1 if entry.active else 0,
        "severity": entry.severity,
        # Milliseconds, not format_metric's 10 significant digits: those
        # would round a Unix time (10 digits already) to whole seconds.
        "raised_at_s": f"{entry.raised_at:.3f}",
        "cleared_at_s": f"{entry.cleared_at:.3f}" if entry.cleared_at is not None else 0,
        "duration_s": entry.duration_s,
        "times_raised": entry.times_raised,
        "schema_version": FAULTS_SCHEMA_VERSION,
    }
    status.values = [KeyValue(key=key, value=format_metric(value)) for key, value in values.items()]
    return status


def build_faults_array(stamp, entries: list[FaultEntry]) -> DiagnosticArray:
    """Every fault in `entries` as one DiagnosticArray stamped `stamp` (empty when there are none)."""
    array = DiagnosticArray()
    array.header.stamp = stamp
    array.status = [fault_status(entry) for entry in entries]
    return array
