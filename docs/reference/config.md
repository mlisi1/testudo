# Configuration

Testudo reads `~/.config/testudo/config.yaml` (or `$XDG_CONFIG_HOME/testudo/config.yaml`), or the file passed with `-c`. Every section is optional. A malformed file stops Testudo at startup with a specific error.

[`config/example_config.yaml`](https://github.com/mlisi1/testudo/blob/main/config/example_config.yaml) is a complete, commented example.

## Top-level keys

| Key | Default | Meaning |
|---|---|---|
| `severity_mode` | `worst` | `worst`, `weighted` or `both`. See [the overall verdict](../concepts/severity.md#the-overall-verdict) |
| `check_undeclared_topics` | `true` | `false` gives content checks only to declared topics |
| `max_check_rate_hz` | `10.0` | Most content checks per topic per second |
| `debounce_samples` | `3` | Samples in a row needed to change a severity |
| `plugins_dir` | none | Directory of [light-path plugins](../developers/plugin-guide.md) |
| `exclude_topics` | `[]` | Rules for the [excluded tier](../concepts/tiers.md#excluded-tier) |

## `topics`

Keyed by message type. Topic names are absolute, and a name may only be declared once.

```yaml
topics:
  nav_msgs/msg/Odometry:
    - name: /odom
      weight: 2
      frame_role: continuous
      event_driven: false
      related_topics:
        cmd_vel: /cmd_vel
      thresholds:
        position_covariance_trace: {warn: 0.02}
        rate_hz: {warn: 25.0, error: 10.0}
      codes:
        LOC-006: ok
```

| Key | Meaning |
|---|---|
| `name` | The topic. Required |
| `weight` | Its weight in `weighted` mode. Default 1 |
| `thresholds` | Per metric: `warn`, `error`, and the meter zones `green`, `orange`, `red` |
| `codes` | Per code: `ok`, `warn` or `error` |
| `related_topics` | Other topics the plugin reads, by logical name. See each plugin's page |
| `event_driven` | `true` when silence is normal |
| `frame_role` | Localization only: `continuous` or `global` |

Any declared topic also accepts the metrics `rate_hz`, `stamp_lag_s` and `stamp_future_s`.

## `actions`

```yaml
actions:
  - name: navigate_to_pose
    weight: 3
    related_topics:
      feedback: /navigate_to_pose/_action/feedback
    thresholds:
      success_rate: {warn: 0.9}
    codes:
      ACT-001: warn
```

## `tf`

The frame pairs that must stay connected.

```yaml
tf:
  - {parent: map, child: odom}
```

## `publish`

| Key | Default | Meaning |
|---|---|---|
| `rate_hz` | `5.0` | How often diagnostics are computed and published |
| `per_plugin` | `true` | One topic per plugin. `false` publishes everything on `topic` |
| `namespace` | `/testudo` | Prefix of the per-plugin topics |
| `topic` | `/diagnostics` | Used when `per_plugin` is `false` |

## `node_profiling`

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Profile ROS nodes |
| `poll_interval_seconds` | `2.0` | How often each process is sampled |
| `rediscovery_interval_seconds` | `5.0` | How often nodes are matched to processes |
| `gpu_enabled` | `true` | Read GPU memory where available |
| `publish_topic` | `/testudo/nodes` | Where results are published |
| `exclude_nodes` | `[]` | Nodes to leave out |
| `thresholds` | see [node profiling](../core-checks/node.md#settings) | `warn` and `error` only |
| `nodes` | `{}` | One node's own `thresholds`, by its full name, over the ones above |

## `faults`

| Key | Default | Meaning |
|---|---|---|
| `retention` | `session` | `session` or `recent`. See [faults](../concepts/faults.md#retention) |
| `recent_window_seconds` | `600` | How long a cleared fault stays under `recent` |
| `publish_topic` | `/testudo/faults` | Where faults are published |

## Changes made from the TUI

[Quick actions](../tui/quick-actions.md), [options](../tui/options.md) and exclude rules are written back to the loaded file. Only the changed value is edited, so your comments and layout stay as they are.

Display preferences are personal, so they go to `display.yaml` beside the default config instead of the robot's file.
