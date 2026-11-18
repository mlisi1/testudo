# Severity and zones

| Severity | Meaning |
|---|---|
| <span class="sev sev-ok">✓ OK</span> | Nothing wrong |
| <span class="sev sev-warn">▲ WARN</span> | Degraded, but working |
| <span class="sev sev-error">✗ ERROR</span> | Something has gone wrong |
| <span class="sev sev-stale">■ STALE</span> | A topic that was publishing has stopped |

Every non-OK status has at least one active code, and each code links to an explanation in these docs.

## Levels decide severity

A metric's `warn` and `error` levels decide its severity. Past `warn` is WARN, past `error` is ERROR, and a level that isn't set never fires. A value exactly on a level is still on the good side.

```yaml
topics:
  nav_msgs/msg/Odometry:
    - name: /odom
      thresholds:
        position_covariance_trace:
          warn: 0.02       # replaces the default warn
          error: null      # removes the default error
```

Only the keys you write change. For most metrics higher is worse. A few are the other way round, such as `success_rate`, `rate_hz` and `size_drop_ratio`.

## Zones only color meters

`green`, `orange` and `red` under the same metric set where the Detail Panel's meters change color. They never change a severity.

```yaml
topics:
  nav_msgs/msg/Odometry:
    - name: /odom
      thresholds:
        position_covariance_trace: {warn: 0.02, green: 0.02, orange: 0.2}
```

## Overriding one code

`codes:` sets the severity of one code on one topic, to `ok`, `warn` or `error`. `ok` hides the code entirely.

```yaml
topics:
  sensor_msgs/msg/NavSatFix:
    - name: /gps/fix
      codes:
        GNSS-001: warn
```

## The overall verdict

`severity_mode` decides how topics roll up:

| Mode | Overall severity |
|---|---|
| `worst` | The worst topic decides. This is the default |
| `weighted` | The average, using each topic's `weight` |
| `both` | The worst decides, with the weighted health shown beside it |
