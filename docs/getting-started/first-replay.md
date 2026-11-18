# Replay a bag

Testudo runs the same checks over a recorded bag, without playing it onto the ROS graph.

## A report

```bash
testudo replay my_bag/
```

This prints each topic's final status and every fault raised during the bag, with its times. The exit code is `0` for OK, `1` for WARN and `2` for anything worse.

## The dashboard

```bash
testudo replay --watch my_bag/
```

The dashboard opens paused on the last frame. Press ++p++ to play, ++bracket-left++ and ++bracket-right++ to step a frame, and ++brace-left++ and ++brace-right++ to step ten.

See [Bag post-mortem](../guides/post-mortem.md) for a full investigation.
