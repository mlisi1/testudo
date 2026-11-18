# Debouncing

A topic's severity changes only after `debounce_samples` samples in a row disagree with it, 3 by default. A single odd reading therefore doesn't flash a fault on screen.

```yaml
debounce_samples: 3
```

Raise it to stop a flapping status. Lower it to react sooner.

One-off events, such as a pose jump or a clipped IMU axis, would otherwise be gone before they could be confirmed. Plugins hold these active for 10 s, so debouncing can confirm them and you have time to read them.
