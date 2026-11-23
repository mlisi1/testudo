# Monitoring tiers

Testudo gives each topic the cheapest monitoring that can still judge it.

| Tier | What it does | Which topics |
|---|---|---|
| **Full** | Deserializes messages and runs a plugin's content checks | Any topic a plugin covers, except undeclared cameras and point clouds |
| **Vitals** | Raw subscription: rate, age and liveness, with no deserialization | Topics no plugin covers |
| **Presence** | No subscription: only checks that a publisher exists | Undeclared cameras and point clouds |
| **Excluded** | Nothing: a name-only row | Topics matching `exclude_topics` |

## Full tier

Content checks run at most `max_check_rate_hz` times per second per topic, 10 by default, however fast the topic publishes. Rate and liveness still see every message. A few plugins read every message because skipping one would lose information, such as TF, the behavior tree log, goal status and velocity commands.

On a very large stack, set `check_undeclared_topics: false` so only declared topics get content checks.

## What monitoring costs

Every message on a full or vitals topic still runs a short callback on Testudo's one receiving thread. On Jazzy and newer, Testudo uses rclpy's events executor. On a robot publishing about 4,700 messages a second, with seven topics at 400 Hz, it takes about a fifth of one CPU core. Humble has no events executor, and the older executor takes about a whole core for the same load. Each topic's rate is averaged over at least its last second of messages.

If the events executor misbehaves, which rclpy still marks experimental, set `TESTUDO_EXECUTOR=single` to fall back to the older one.

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
