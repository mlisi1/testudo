# Velocity command

Judges what a controller asks of the robot, and whether the robot responds.

## What it covers

| | |
|---|---|
| Message types | `geometry_msgs/msg/Twist`, `geometry_msgs/msg/TwistStamped` |
| Tier | Full, declared or not. Every message is read, and silence is always allowed |
| TUI category | Velocity Commands |
| Published on | `/testudo/velocity_command` |

## What it needs

Testudo never guesses which odometry is authoritative, so the stuck-robot check needs it named:

| Related topic | Adds |
|---|---|
| `odom` | [`VEL-002`](#vel-002) |
| `goal_status` | Limits [`VEL-002`](#vel-002) and [`VEL-004`](#vel-004) to the time a goal is executing, which leaves teleoperation alone |

```yaml
topics:
  geometry_msgs/msg/Twist:
    - name: /cmd_vel_nav
      related_topics:
        odom: /odom
        goal_status: /navigate_to_pose/_action/status
```

## Codes

### VEL-001 · NaN or Inf in a command { #vel-001 }

<span class="sev sev-error">✗ ERROR</span> A velocity field is not a finite number. The code stays active for 10 s.

**Likely causes:** a controller dividing by zero, or an invalid input to it.

**What to do:** treat it as a controller bug, because a base driver may act on the value.

### VEL-002 · Commanded to move, robot isn't moving { #vel-002 }

<span class="sev sev-warn">▲ WARN</span> <span class="sev sev-error">✗ ERROR</span> Commands have asked for motion for longer than `stuck_s` while odometry shows less than 10% of the commanded speed.

**Likely causes:** a blocked robot, disabled motors or an emergency stop.

**What to do:** check the robot and its base driver.

### VEL-003 · Command reversing direction { #vel-003 }

<span class="sev sev-warn">▲ WARN</span> <span class="sev sev-error">✗ ERROR</span> The linear or angular command changes sign more often than `reversals_per_s`, counted over 10 s.

**Likely causes:** an oscillating controller.

**What to do:** review the controller's tuning and its critics.

### VEL-004 · Turn rate pinned at one value { #vel-004 }

<span class="sev sev-warn">▲ WARN</span> <span class="sev sev-error">✗ ERROR</span> The angular command has held exactly the same non-zero value for longer than `pinned_s`. Cruising at a constant forward speed is normal, so only the turn rate counts.

**Likely causes:** a controller saturated at its angular limit.

**What to do:** check the limits against what the path demands.

## Settings

--8<-- "levels/velocity-command.md"

## In the TUI

The Topic Panel adds a `Cmd` column showing the linear and angular command.

## Known limits

- Without an `odom` related topic, [`VEL-002`](#vel-002) never fires.
- Default levels are first estimates and have not yet been validated on a real robot.
