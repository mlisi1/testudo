<p align="center">
  <img src="docs/logo_wordmark.svg" alt="Testudo" width="640">
</p>

<p align="center"><b>Black-box diagnostics for ROS 2 navigation stacks, live in the terminal or from a bag.</b></p>

<p align="center">
  <a href="https://mlisi1.github.io/testudo/">Documentation</a> ·
  <a href="https://mlisi1.github.io/testudo/getting-started/">Getting started</a> ·
  <a href="https://mlisi1.github.io/testudo/tui/explore/">Try the dashboard</a>
</p>

<p align="center">
  <img src="docs/media/testudo_replay.gif" alt="The Testudo dashboard playing back a recorded field run and jumping between problems" width="960">
</p>

Testudo watches a running robot from the outside, with no changes to its nodes. It tells you which topics are alive, whether sensors and odometry report believable data, whether the TF tree holds together, and how Nav2's goals end.

- **No config needed.** Every topic a plugin understands is checked as soon as it appears.
- **Quiet is not dead.** Latched topics, on-demand topics and idle inputs are told apart from a publisher that died.
- **Every problem explained.** Each code has a page with its likely causes and what to do.
- **Live, headless or from a bag.** Use the dashboard, publish standard `DiagnosticArray` topics on a robot, or fail a CI run from a recorded bag.
- **Extensible.** The built-in checks use the same plugin interface yours would.

## Quick start

Testudo is a colcon package, developed on ROS 2 Jazzy.

```bash
source /opt/ros/jazzy/setup.bash
mkdir -p ~/your_ws/src && cd ~/your_ws/src
git clone https://github.com/mlisi1/testudo.git
cd ~/your_ws
rosdep update
rosdep install --from-paths src --ignore-src -r -y --skip-keys python3-textual
python3 -m pip install --user --break-system-packages textual
colcon build --packages-select testudo
source install/setup.bash
```

Ubuntu's own `python3-textual` is too old for the dashboard, so a newer one goes in your home directory.

```bash
testudo watch                    # the live dashboard: ? lists the keys, q quits
testudo watch --headless         # the same checks with no dashboard
testudo replay my_bag/           # a report on a bag, with an exit code for CI
testudo replay --watch my_bag/   # scrub through a bag in the dashboard
```

In a simulator, add `--ros-args -p use_sim_time:=true`.

## Documentation

The [documentation](https://mlisi1.github.io/testudo/) covers the [dashboard](https://mlisi1.github.io/testudo/tui/), [every check](https://mlisi1.github.io/testudo/reference/codes/), the [configuration](https://mlisi1.github.io/testudo/reference/config/) and [writing your own plugin](https://mlisi1.github.io/testudo/developers/plugin-guide/).

## License

Apache License 2.0. See [LICENSE](LICENSE).
