# Watch a running stack

With your stack running, in a terminal on the same `ROS_DOMAIN_ID`:

```bash
testudo watch
```

In a simulator, tell Testudo to use the simulation clock:

```bash
testudo watch --ros-args -p use_sim_time:=true
```

No configuration is needed. Every topic a plugin covers gets its content checks straight away.

## Reading the dashboard

1. The top bar shows the overall verdict.
2. The left pane lists categories, and below them **Needs attention** lists the active codes, worst first.
3. Press ++n++ to jump to the next topic with a problem.
4. The bottom pane explains the highlighted topic and its active codes.

Press ++question++ for every key, and ++q++ to quit. The [TUI guide](../tui/index.md) covers the rest.
