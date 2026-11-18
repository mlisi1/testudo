# Liveness

A silent topic isn't always a dead one. Testudo separates the cases below.

| Status | Meaning | How it's recognized |
|---|---|---|
| `● latched` | Sent once, stays valid | The publisher's QoS is `transient_local` |
| `◌ idle` | Publishes only when something happens | Its message type, or `event_driven: true` |
| `◌ input` | Nodes subscribe, nobody publishes yet | Undeclared, and never had a publisher this session |
| `◌ present` | A publisher exists, not subscribed | The [presence tier](tiers.md#presence-tier) |
| [`LIVE-003`](../core-checks/live.md#live-003) | It was publishing and has stopped | No message for 2 s |

## Topics that may go quiet

These are idle by default:

- Behavior tree logs, goal status, paths, costmap updates and visualization markers.
- DWB's trajectory evaluations, bond heartbeats and a recorder's split events.
- Every `*/_action/feedback` topic.

Declare anything else that only publishes on demand:

```yaml
topics:
  geometry_msgs/msg/PoseStamped:
    - name: /my_stack/on_demand_pose
      event_driven: true
```

An undeclared topic that has never published is idle too. Declaring it makes that silence an error.

## Inactive lifecycle nodes

Topics that belong to an `inactive` or `unconfigured` lifecycle node are grayed out instead of flagged.

## A stack that closes

When 90% of the topics have gone quiet for 15 s, Testudo assumes the stack was closed and clears its rows. A relaunched stack is picked up automatically.
