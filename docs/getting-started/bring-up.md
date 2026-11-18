# Bring up a robot

Testudo works over SSH, so you can run it on the robot itself.

1. Start your stack, then run `testudo watch`.
2. Work through **Needs attention** from the top. Press ++n++ to go to each problem.
3. Read the code in the Detail Panel, and look it up in [all codes](../reference/codes.md).
4. Fix the cause, or tune the check if the reading is normal for your robot.

## Tuning from the dashboard

When a reading is normal for your robot, press ++a++ on the topic. You can set the warning level from the current value, downgrade or mute the code, or exclude the topic. Each change takes effect at once and is saved to your config. See [quick actions](../tui/quick-actions.md).

## Declaring what matters

A declared topic is expected to be there, so its absence is an error. Run [`testudo config init`](config-init.md) while the stack is healthy, then add the related topics that unlock more checks:

```yaml
topics:
  geometry_msgs/msg/Twist:
    - name: /cmd_vel_nav
      related_topics:
        odom: /odom
tf:
  - {parent: map, child: base_link}
```
