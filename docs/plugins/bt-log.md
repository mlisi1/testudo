# Behavior tree log

Shows what Nav2's behavior tree is doing, and how often it falls back on recoveries.

## What it covers

| | |
|---|---|
| Message types | `nav2_msgs/msg/BehaviorTreeLog` |
| Tier | Full, declared or not. Every message is read |
| TUI category | BT Navigator |
| Published on | `/testudo/bt_log` |

## What it needs

`nav2_msgs` must be installed. Without it, the plugin stays inactive and everything else keeps working.

It counts recovery nodes whose name starts with `Spin`, `BackUp`, `Wait`, `DriveOnHeading`, `ClearEntireCostmap`, `ClearCostmapExceptRegion` or `ClearCostmapAroundRobot`. This includes recoveries wired into the tree as service calls, which the [Actions plugin](actions.md) can't see.

```yaml
topics:
  nav2_msgs/msg/BehaviorTreeLog:
    - name: /behavior_tree_log
      thresholds:
        recovery_frequency_per_min: {warn: 2.0}
```

## Codes

### BT-001 · Recovering too often { #bt-001 }

<span class="sev sev-warn">▲ WARN</span> <span class="sev sev-error">✗ ERROR</span> Recovery nodes trigger more often than `recovery_frequency_per_min`. It has no default, because how often recovery is normal depends on the site and on the tree.

**Likely causes:** a planner or controller repeatedly failing, or a cluttered environment.

**What to do:** the Detail Panel names the last recovery and the last node that failed.

## Settings

--8<-- "levels/bt-log.md"

## In the TUI

The Topic Panel adds a `Rec/m` column. The Detail Panel shows the running branch, recent recoveries and the last failure.

## Known limits

- This plugin assumes Nav2's message type and recovery node names. A custom recovery node with another name is not counted.
