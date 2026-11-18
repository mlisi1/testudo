# Bag post-mortem

To find what broke first in a recorded run:

1. Run `testudo replay my_bag/` and read **Faults during the bag**. Faults are sorted by when they were raised, so the first ones are usually closest to the cause.
2. Open the bag with `testudo replay --watch my_bag/`.
3. Press ++p++ to play, or step with ++bracket-left++ and ++bracket-right++, up to the moment of the first fault.
4. Use ++n++ to move between the topics with problems at that moment.

The report and the dashboard use the same checks as a live session. Node profiling isn't available, because a bag has no processes to sample.

!!! note "Short faults"
    The dashboard samples a bag at least 1 s apart, with at most 600 frames, so a very short fault can fall between two frames.

Use the same config as the robot with `-c`, so levels and declared topics match:

```bash
testudo replay -c robot_config.yaml my_bag/
```
