# Headless and launch

`--headless` runs the same checks without the dashboard, and publishes diagnostics until stopped.

```bash
testudo watch --headless
```

## From a launch file

```bash
ros2 launch testudo testudo.launch.py config:=/path/to/testudo.yaml use_sim_time:=true
```

| Argument | Default | Meaning |
|---|---|---|
| `config` | the default location | The config file |
| `use_sim_time` | `false` | Use `/clock` |

Read the results from the [published topics](../reference/topics.md), for example in PlotJuggler or Foxglove, or record them with `ros2 bag record`.

!!! warning "Long runs"
    Headless mode hasn't had a long-duration test yet, so always-on monitoring of a robot isn't recommended for now.
