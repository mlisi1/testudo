# The dashboard

Everything on screen, piece by piece. Four panes stay visible at once, and moving the cursor updates the others, so nothing needs opening.

--8<-- "dashboard/overview.md"

Click a pane to go to its section. The screenshot shows a small stack that misbehaves on purpose, with a camera topic highlighted.

## Status Bar

The state of the session on the left, and the verdict on the whole stack on the right.

--8<-- "dashboard/status-bar.md"

The verdict is one of these:

| Verdict | Meaning |
|---|---|
| <span class="sev sev-ok">✓ NOMINAL</span> | Every topic is OK |
| <span class="sev sev-warn">▲ DEGRADED</span> | Something is working worse than it should |
| <span class="sev sev-error">✗ FAULT</span> | Something has gone wrong. This wins over STALE whenever any topic is in error |
| <span class="sev sev-stale">■ STALE</span> | A topic stopped publishing, and none is in error |

With `severity_mode: weighted`, the average of all topics picks the verdict instead of the worst one.

These appear only when they apply:

| On screen | When |
|---|---|
| `clock sim` | The session [uses simulated time](../getting-started/index.md#2-watch-your-stack) |
| `bag ▶ 1m42s/2m35s` | You are [scrubbing a bag](replay.md). `‖` replaces `▶` while paused |
| `PAUSED` | You pressed ++p++ |

## Plugin Panel

What is being watched, and what to look at first.

--8<-- "dashboard/plugin-panel.md"

These appear only when they apply:

| On screen | When |
|---|---|
| **Cleared** | There is room under **Needs attention** and a [fault has cleared](../concepts/faults.md). Each line reads `✗ GNSS-002 2m /gps`: the worst severity it reached, the code, how long ago it cleared and the topic |
| **Other Topics** | Some topics have no plugin. They get [vitals monitoring](../concepts/tiers.md) only |
| **Excluded Topics** | You [excluded topics](options.md#exclude-topics). The row counts them, since nothing in it is checked |

## Topic Panel

Every topic in the highlighted category.

--8<-- "dashboard/topic-panel.md"

A topic with nothing wrong doesn't always read `✓ OK`. [Liveness](../concepts/liveness.md) explains why:

| Status | Meaning |
|---|---|
| `◌ idle` | Quiet by design: it publishes only when something happens |
| `● latched` | Sent once and still valid |
| `◌ input` | Nodes subscribe to it, and nobody publishes it yet |
| `◌ present` | A publisher exists, and Testudo [doesn't subscribe](../concepts/tiers.md#presence-tier) |

The columns change with the category:

| Category | Difference |
|---|---|
| [Actions](../plugins/actions.md) | **State** replaces **Hz**: `▶ run`, `⊘ stop` or `◌ idle` |
| [Nodes](../core-checks/node.md) | **Mem** replaces **Hz**: the node's memory use |
| Other Topics | **Type** shows each topic's message type |
| Excluded Topics | Only the name, because nothing is checked |

When every visible topic shares a namespace, it moves to the column header to leave room for the part of the name that differs.

## Detail Panel

Everything Testudo knows about the highlighted topic. The first line and the active codes look the same for every topic. The values in between come from the plugin, so each [plugin page](../plugins/index.md) explains its own.

--8<-- "dashboard/detail-panel.md"

These appear only when they apply:

| On screen | When |
|---|---|
| `▲ [LOC-002] cleared 2m ago, lasted 5s ×3` | The topic has [cleared faults](../concepts/faults.md) and the pane has room. `×3` counts how many times it was raised |
| `publisher` | Testudo knows who publishes the topic. It comes first on a topic no plugin reads, and last on a tall pane otherwise |
| A map | The topic is a [costmap or occupancy grid](../plugins/costmap.md) and the pane has room |

## Hint bar

The keys you need most. [Keys](keys.md) lists all of them.

--8<-- "dashboard/hint-bar.md"

In a replay, `s sort`, `p play` and `[ ] scrub` take the place of `a actions`, `p pause` and `o options`. See [Replay scrubbing](replay.md).

<div class="tx-cta" markdown>

**Want to see more? Try it.**

Move around a working dashboard right here in your browser, with nothing to install.

[Explore the dashboard](explore.md){ .md-button .md-button--primary }

</div>
