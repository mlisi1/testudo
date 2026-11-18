# Faults topic schema

`/testudo/faults` carries one `DiagnosticStatus` per fault, republished on every poll. Plotting a fault's `level` shows when it was active. This page describes schema version 1.

## Status fields

| Field | Content |
|---|---|
| `name` | `testudo: <topic> <CODE>` |
| `level` | The fault's severity right now, or OK once it has cleared |
| `message` | The code's latest message, as plain text |
| `hardware_id` | `testudo` |

## Values

| Key | Content |
|---|---|
| `topic` | The topic with the fault |
| `code` | The code, such as `GNSS-002` |
| `active` | `1` while active, `0` once cleared |
| `severity` | The worst severity it reached: 1 WARN, 2 ERROR, 3 STALE |
| `raised_at_s` | When it was raised, in Unix seconds |
| `cleared_at_s` | When it cleared, in Unix seconds, or `0` while active |
| `duration_s` | How long it has been, or was, active |
| `times_raised` | How many times it was raised this session |
| `schema_version` | `1` |

The version only increases when a key changes meaning or disappears. New keys can be added without changing it, so a consumer should ignore keys it doesn't know.
