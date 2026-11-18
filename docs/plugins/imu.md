# IMU

Checks that an IMU's readings are valid, alive and physically sensible, with no setting for how it is mounted or whether it removes gravity.

## What it covers

| | |
|---|---|
| Message types | `sensor_msgs/msg/Imu` |
| Tier | Full, declared or not |
| TUI category | IMU |
| Published on | `/testudo/imu` |

## What it needs

Nothing, for most checks. One optional related topic adds gyro bias:

| Related topic | Adds |
|---|---|
| `odom` | [`IMU-010`](#imu-010): a steady gyro reading alone can't tell bias from a constant turn |

```yaml
topics:
  sensor_msgs/msg/Imu:
    - name: /imu/data
      related_topics:
        odom: /odom
```

## Codes

### IMU-001 · NaN or Inf in the readings { #imu-001 }

<span class="sev sev-error">✗ ERROR</span> An acceleration or angular velocity field is not a finite number.

**Likely causes:** a driver fault, or a failed filter upstream.

**What to do:** check the driver's output and connection to the sensor.

### IMU-002 · Reading stuck { #imu-002 }

<span class="sev sev-warn">▲ WARN</span> <span class="sev sev-error">✗ ERROR</span> All six acceleration and gyro fields have been identical for more consecutive checked messages than `stuck_count` allows. Real sensor noise makes this very unlikely.

**Likely causes:** a frozen driver republishing its last sample.

**What to do:** restart the driver and check the link to the sensor.

### IMU-003 · Implausible magnitude { #imu-003 }

<span class="sev sev-warn">▲ WARN</span> Acceleration exceeds 100 m/s², or angular velocity exceeds 50 rad/s.

**Likely causes:** wrong units, a corrupted message, or a violent impact.

**What to do:** compare the raw values with the sensor's datasheet units.

### IMU-004 · Frame id changed { #imu-004 }

<span class="sev sev-warn">▲ WARN</span> `header.frame_id` differs from an earlier message on this topic. It stays active until stats are reset with ++r++.

**Likely causes:** two publishers on one topic, or a reconfigured driver.

**What to do:** check how many nodes publish the topic.

### IMU-005 · Wrong acceleration at rest { #imu-005 }

<span class="sev sev-warn">▲ WARN</span> While the IMU looks still for 2 s, the length of its acceleration is neither about 9.81 m/s² nor about 0. A value near 1 suggests the driver reports in g.

**Likely causes:** wrong units or a scale error.

**What to do:** fix the driver's unit setting. Mounting and slope don't affect this check, because only the length is judged.

### IMU-006 · Gravity mode changed { #imu-006 }

<span class="sev sev-warn">▲ WARN</span> The acceleration at rest switched between including and removing gravity, or between units. It stays active for 10 s.

**Likely causes:** a driver reconfigured at runtime, or two publishers with different settings.

**What to do:** check the driver's configuration and the publishers on the topic.

### IMU-007 · Orientation quaternion not normalized { #imu-007 }

<span class="sev sev-warn">▲ WARN</span> <span class="sev sev-error">✗ ERROR</span> The orientation's norm differs from 1 by more than `quaternion_norm_error`. An orientation marked as absent by REP 145 is not judged.

**Likely causes:** a driver or filter that doesn't normalize its output.

**What to do:** fix the publisher, since consumers such as TF can reject the quaternion.

### IMU-008 · Covariance left at zero { #imu-008 }

<span class="sev sev-warn">▲ WARN</span> An orientation, angular velocity or acceleration covariance is all zero, which claims a perfect sensor. REP 145's `-1` for "not provided" is fine.

**Likely causes:** a driver that never fills the covariance, or a configured standard deviation of 0.

**What to do:** set realistic values in the driver. If a fusion node ignores them, you can accept this with `codes: {IMU-008: ok}` on the topic.

### IMU-009 · Axis clipped at its range limit { #imu-009 }

<span class="sev sev-warn">▲ WARN</span> <span class="sev sev-error">✗ ERROR</span> One axis repeats the same large value (at least 19.6 m/s² or 2 rad/s) for more samples than `saturated_samples` allows, while the other axes still change. It stays active for 10 s.

**Likely causes:** motion beyond the sensor's configured range.

**What to do:** raise the sensor's full-scale range, or reduce vibration at the mount.

### IMU-010 · Gyro bias { #imu-010 }

<span class="sev sev-warn">▲ WARN</span> <span class="sev sev-error">✗ ERROR</span> The gyro's mean reading exceeds `gyro_bias_radps` after odometry has reported the robot still for 2 s.

**Likely causes:** an uncalibrated gyro, or temperature drift.

**What to do:** recalibrate the gyro at rest.

### IMU-011 · Stamp gaps or jitter { #imu-011 }

<span class="sev sev-warn">▲ WARN</span> <span class="sev sev-error">✗ ERROR</span> The interval between stamps exceeds `stamp_gap_ratio` times the usual one, a stamp went backwards, or the intervals vary by more than `stamp_jitter_ratio`. Gaps and backward steps stay active for 10 s.

**Likely causes:** dropped samples, a congested bus, or a host-stamped driver under load.

**What to do:** check the sensor's link and the load on the host. Only the checked messages are seen, so very short gaps can pass unnoticed.

## Settings

--8<-- "levels/imu.md"

## In the TUI

The Topic Panel adds an `|a|` column. The Detail Panel shows acceleration, angular velocity and a recent gyro trend.

## Known limits

- A wrongly mounted IMU with a correct acceleration length is not detected.
- Checks run at the decimated check rate, 10 Hz by default.
- Default levels are first estimates and have not yet been validated on a real robot.
