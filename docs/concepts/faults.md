# Faults

A fault is one code active on one topic. Testudo remembers each fault after it clears, so a problem that came and went isn't lost.

- The TUI lists cleared faults in the Plugin Panel and in the topic's Detail Panel.
- A live session publishes them on [`/testudo/faults`](../reference/faults-schema.md).
- `replay` lists every fault raised during the bag.

## Retention

```yaml
faults:
  retention: session          # or: recent
  recent_window_seconds: 600
```

| `retention` | Cleared faults kept |
|---|---|
| `session` | All of them this session, up to 200 |
| `recent` | Each one drops off `recent_window_seconds` after clearing |

Active faults always stay. Pressing ++r++ clears the history. A topic that disappears from monitoring, for example because you excluded it, takes its faults with it, because nothing was fixed.
