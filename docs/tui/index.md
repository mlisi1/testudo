# The dashboard

Four panes are visible at once. Moving the cursor updates the others, so nothing needs opening.

--8<-- "anatomy.md"

## Status Bar

The verdict on the right reads `✓ NOMINAL`, `▲ DEGRADED`, `✗ FAULT` or `■ STALE`, followed by the number of topics with problems.

## Plugin Panel

Each row shows how many topics are at each severity, such as `✗2 ▲1`. **Needs attention** lists up to five active codes, worst first. **Cleared** lists recently cleared ones and how long ago they cleared.

## Topic Panel

Every category shows the topic's name, status and rate. A plugin can add one column of its own, such as `σxy` for localization.

| Status | Meaning |
|---|---|
| `◌ idle` | Quiet by design |
| `● latched` | Sent once and still valid |
| `◌ input` | Nodes subscribe, nobody publishes |
| `◌ present` | A publisher exists, not subscribed |

## Detail Panel

Shows the status, every value the plugin reports, and each active code with its message. Below these are the topic's cleared faults, when there is room.
