# Testudo

**Black-box diagnostics for ROS 2 navigation stacks — neofetch for your nav stack.**

Testudo watches a running robot from the outside: no instrumentation of
existing nodes required. It reports on topic health, sensor sanity,
odometry/covariance quality, TF integrity, and Nav2 goal/action outcomes,
then shows you the result in a live terminal UI, a one-shot CI-friendly
report, or a standard `diagnostic_msgs/DiagnosticArray` you can pipe into
rqt_robot_monitor, Foxglove, or PlotJuggler.

It fills the gap `diagnostic_updater`/`diagnostic_aggregator` leave for
everything you *can't* or don't want to instrument — drivers, Nav2
internals, third-party nodes — and for semantic checks (goal outcomes,
covariance sanity, TF tree health) those tools don't cover out of the box.

## Features

- **Two-tier subscription model, with a presence-only escape hatch for
  heavy topics.** Every topic on the graph gets watched for liveness and
  frequency for free (raw subscriptions, no deserialization). Topics you
  declare — or that a plugin recognizes by message type — additionally
  get full content checks. A plugin can mark specific message types as
  presence-only by default instead (`CheckPlugin.presence_only_msg_types`)
  when even a raw subscription is too heavy to auto-watch on every
  undeclared topic: no subscription at all, just the publisher-exists
  check every topic already gets, unless you declare that topic under
  `topics:`. Image/CompressedImage (`ImageStreamPlugin`) and 3D
  PointCloud2 (`PointStreamPlugin`) default this way; 2D LaserScan stays
  on the normal full tier. `theora_image_transport/msg/Packet` is
  presence-only too (no dedicated plugin) — subscribing to a lazy
  `image_transport` republisher can wake up an otherwise-idle encoder. An
  `exclude_topics:` list in config (literal names or regex patterns) drops
  matching topics from real monitoring entirely; they still show up, name
  only, in their own **Excluded Topics** category rather than vanishing,
  and can be managed live from the TUI's Options screen (`o`), which
  persists a rule back to the config file so it survives a restart.
- **Built-in content checks**, all through the same plugin interface
  external users build against:

  | Plugin | Message type | Checks |
  |---|---|---|
  | Odometry | `nav_msgs/msg/Odometry` | covariance threshold zones, positive-semi-definiteness, growth-rate sanity, optional `cmd_vel` cross-check |
  | Generic sensor | `sensor_msgs/msg/Imu` | NaN/Inf, stuck values, magnitude plausibility, `frame_id` consistency |
  | Point stream | `sensor_msgs/msg/LaserScan` (2D, full tier), `sensor_msgs/msg/PointCloud2` (3D, presence-only by default) | NaN/Inf/out-of-range ratio, stuck reading, `frame_id` consistency, malformed/empty payload (3D only) |
  | Image stream | `sensor_msgs/msg/Image`, `sensor_msgs/msg/CompressedImage` (both presence-only by default) | stuck/empty frame, malformed payload, size-drop, `frame_id` consistency, rolling bandwidth |
  | Nav2 goals | `action_msgs/msg/GoalStatusArray` | goal lifecycle, success rate, mean duration, invocation frequency — covers both navigation goals and recovery behaviors |
  | TF watch | `tf2_msgs/msg/TFMessage` | missing chains, multi-parent frames, stale-edge (extrapolation-risk) detection |

  Actual-vs-configured *rate* checks (a planner/controller/costmap
  publishing slower than expected) need no dedicated plugin at all —
  declare a `rate_hz` threshold on any topic.
- **Lifecycle-aware.** Diagnostics for a topic owned by an
  `inactive`/`unconfigured` lifecycle node are suppressed rather than
  flagged red.
- **`use_sim_time`-aware** staleness/frequency checks.
- **Hysteresis debouncing** so a single noisy sample doesn't flap a
  status, plus configurable **worst / weighted / both** severity
  aggregation.
- **A live TUI** (`testudo watch`) — four panes visible at once (status
  bar, plugin panel, topic panel, detail panel), all updating as you move
  the cursor rather than a list you drill into one entry at a time —
  filter, sort, pause, reset stats — and the same view for a finished
  bag (`testudo replay --watch`).
- **Standard output.** Publishes `diagnostic_msgs/DiagnosticArray`,
  decoupled from each check's own sampling rate — interoperable with
  rqt_robot_monitor, Foxglove, PlotJuggler, and bag-recordable for free.
- **Extensible.** Drop a `.py` file with a decorated class into a
  configured directory (no packaging needed), or ship a plugin as its own
  installable package via a `testudo.checks` entry point. Built-in
  plugins use the exact same interface external users get.

## Example

```
$ testudo check
[WARN ] /odom                          [full  ] nav_msgs/msg/Odometry               position covariance trace 0.06 out of bounds
[OK   ] /scan                          [full  ] sensor_msgs/msg/LaserScan           0 invalid, 0 out-of-bounds of 8
[ERROR] /local_costmap/costmap         [vitals] nav_msgs/msg/OccupancyGrid          rate 0.50Hz below configured threshold

[ERROR] 3 topic(s): OK=1, WARN=1, ERROR=1
```

`testudo watch` shows the same data live, in four panes visible at once:
a **status bar** (hostname, ROS distro, uptime, pause state), a **plugin
panel** (one row per category with a per-severity icon+count breakdown),
a **topic panel** (every topic in the highlighted category, with its
status and publish rate in Hz), and a **detail panel** (the highlighted
topic's full status). Moving the cursor — not pressing `Enter` — is what
drives the other panes live; `Enter`, `Tab`/`Shift+Tab`, or `←`/`→`
switch focus between the plugin and topic panels, `Esc` refocuses the
plugin panel (or clears an active filter), `/` to filter the focused
panel, `s` to cycle its sort order,
`p` to pause, `r` to reset accumulated stats, `o` for the options menu
(currently: manage `exclude_topics` rules live), `?` for help. Any
warning/error logged while the TUI has the terminal (most commonly a
message type whose interface package isn't installed) is buffered rather
than printed live — a stray write to the terminal while Textual is
rendering corrupts the display — and summarized once the session ends
(deduplicated, with a repeat count).

## Requirements

- ROS 2 Humble or newer (primary development/test target: **Jazzy**)
- Python 3.10+
- [Textual](https://github.com/Textualize/textual) for `testudo watch` —
  `rosdep`'s `python3-textual` resolves to an apt package far too old to
  work; install a current one with `pip install textual` (also pulled in
  automatically by `pip install -e .`).

## Installation

```bash
cd ~/your_ws/src
git clone git@github.com:mlisi1/testudo.git
cd ~/your_ws
rosdep install --from-paths src --ignore-src -r -y
pip install textual   # see note above
colcon build --packages-select testudo
source install/setup.bash
```

## Quick start

Every command reads `~/.config/testudo/config.yaml` (or
`$XDG_CONFIG_HOME/testudo/config.yaml`) by default, or a path given via
`-c`/`--config`. No file at either location isn't an error — every config
section is optional, so it's the same as an empty one: vitals tier only,
nothing declared or excluded. The Options screen's live exclude rules
(`o` in `watch`) are written back to whichever config is in effect,
creating that default file and its parent directory the first time you
add one if neither exists yet.

```bash
# One-shot report for CI / pre-flight checks. Exit codes: 0 OK, 1 WARN, 2 ERROR/STALE.
testudo check

# Live TUI.
testudo watch

# Batch report over a recorded bag, or --watch to browse it in the TUI.
testudo replay --watch my_bag/

# What plugins are available, and what they cover.
testudo plugins

# Point at a specific config instead of the default -- e.g. this repo's
# own reference example, to see a realistic set of declared topics/
# actions/tf pairs and threshold overrides.
testudo watch -c config/example_config.yaml
```

Every command validates its config up front and fails loudly with a
specific error if it's malformed, rather than failing three modules deep
with a `KeyError`.

## Configuration

Three top-level sections, all optional — anything you don't declare still
gets the cheap vitals tier automatically:

```yaml
severity_mode: worst   # worst | weighted | both

topics:
  nav_msgs/msg/Odometry:
    - name: /odom
      weight: 2
      related_topics:
        cmd_vel: /cmd_vel
      thresholds:
        position_covariance_trace: {green: 0.02, orange: 0.2}

actions:
  - name: navigate_to_pose
    action_type: nav2_msgs/action/NavigateToPose
    thresholds:
      success_rate: {green: 0.9, orange: 0.7}

tf:
  - {parent: map, child: base_link}

# A plain string is a literal (exact-name) rule; a mapping opts into a
# regex searched against the topic name (`^` / `$` anchor a starts-with /
# ends-with check). A matching topic gets no subscription of any kind --
# just a name-only row in the TUI's "Excluded Topics" category, distinct
# from a topic Testudo never even attempts (its own /parameter_events,
# /rosout self-exclusion, which stays fully invisible). Nothing needs to
# be listed here out of the box -- ImageStream/PointStream already default
# their own heavy message types to presence-only (see above); this is for
# topics *you* want dropped, e.g. one whose interface package isn't
# installed on this host. Rules added live from the TUI's Options screen
# (`o`) are appended here automatically.
#
# A literal pattern containing a character no ROS topic name can have
# (`$ ^ * . + ? ( ) [ ] { } |`, etc.) fails loudly at load time rather
# than silently matching nothing -- that combination almost always means
# a regex rule that forgot `type: regex`.
exclude_topics:
  - /velodyne_packets
  - pattern: "_debug$"
    type: regex
```

See [`config/example_config.yaml`](config/example_config.yaml) for a
complete, commented example.

## Extending Testudo

A check plugin is a small, stable interface:

```python
class CheckPlugin(abc.ABC):
    def msg_types(cls) -> tuple[str, ...]: ...
    def default_thresholds(cls) -> dict[str, ThresholdZone]: ...
    def on_message(self, topic: str, msg: Any) -> None: ...
    def on_tick(self, now_seconds: float) -> None: ...
    def get_status(self) -> CheckStatus: ...
```

Register it two ways: drop a `.py` file decorated with `@register_plugin`
into the directory named by `plugins_dir` in your config (no packaging
needed), or expose it via a `testudo.checks` entry point in your own
installable package. A full plugin-authoring guide is planned; in the
meantime the built-in plugins in
[`testudo/plugins/builtin/`](testudo/plugins/builtin/) are real,
representative examples to build from.

## Status

Testudo is under active development. Discovery, the two-tier subscription
core, all four built-in content plugins, `DiagnosticArray` publishing,
severity aggregation, bag replay, and the TUI are implemented and tested.
See [`CLAUDE.md`](CLAUDE.md) for the full milestone plan and architecture
notes.

## License

Apache License 2.0 — see [LICENSE](LICENSE).
