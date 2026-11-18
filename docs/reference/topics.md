# Published topics

Everything is a standard `diagnostic_msgs/msg/DiagnosticArray`, so it works with PlotJuggler, Foxglove and `ros2 bag record`.

| Topic | Content |
|---|---|
| `/testudo/<plugin>` | One status per topic that plugin checks, such as `/testudo/localization` |
| `/testudo/vitals` | Topics no plugin checks |
| `/testudo/overall` | The roll-up verdict |
| `/testudo/faults` | One status per fault. See the [schema](faults-schema.md) |
| `/testudo/nodes` | CPU, memory and GPU per node |

Plugin names are `localization`, `imu`, `gnss`, `image_stream`, `point_stream`, `velocity_command`, `costmap`, `actions`, `bt_log` and `tf_watch`.

## For rqt_robot_monitor

`rqt_robot_monitor` and `diagnostic_aggregator` listen to `/diagnostics`. This publishes everything there as one array:

```yaml
publish:
  per_plugin: false
```

Node profiling then moves to `/diagnostics/nodes`, and faults to `/diagnostics/faults`.

## Values

Each status is named `testudo: <topic>`. Its values are plain numbers with the unit in the key, such as `rate_hz` and `age_s`. Ratios go from 0 to 1, and booleans are 1 or 0.

Testudo's own output topics are never monitored.
