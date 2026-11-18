# Monitoring tiers

Testudo gives each topic the cheapest monitoring that can still judge it.

| Tier | What it does | Which topics |
|---|---|---|
| **Full** | Deserializes messages and runs a plugin's content checks | Any topic a plugin covers |
| **Vitals** | Raw subscription: rate, age and liveness, with no deserialization | Topics no plugin covers |
| **Presence** | No subscription: only checks that a publisher exists | Undeclared cameras and point clouds |
| **Excluded** | Nothing: a name-only row | Topics matching `exclude_topics` |

## Full tier

Content checks run at most `max_check_rate_hz` times per second per topic, 10 by default, however fast the topic publishes. Rate and liveness still see every message. A few plugins read every message because skipping one would lose information, such as TF, the behavior tree log, goal status and velocity commands.

On a very large stack, set `check_undeclared_topics: false` so only declared topics get content checks.

## Presence tier

Subscribing to camera or point cloud data can compete with the pipeline producing it, and can wake a lazy `image_transport` encoder. Declare one of these topics under `topics:` to opt it into full monitoring.

## Excluded tier

Add a rule in the config, or live from the TUI's [options](../tui/options.md). A rule is an exact topic name, or a regex:

```yaml
exclude_topics:
  - /velodyne_packets
  - pattern: "_debug$"
    type: regex
```

Excluded topics stay visible in their own category, so you remember what you turned off.
