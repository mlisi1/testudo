| Code | What it means |
|---|---|
| [`LOC-001`](../plugins/localization.md#loc-001) | Position covariance too high |
| [`LOC-002`](../plugins/localization.md#loc-002) | Yaw variance too high |
| [`LOC-003`](../plugins/localization.md#loc-003) | Covariance growing |
| [`LOC-004`](../plugins/localization.md#loc-004) | Pose covariance is not positive semi-definite |
| [`LOC-005`](../plugins/localization.md#loc-005) | Twist covariance is not positive semi-definite |
| [`LOC-006`](../plugins/localization.md#loc-006) | Commanded to move, odometry reports no motion |
| [`LOC-007`](../plugins/localization.md#loc-007) | Pose jumped |
| [`LOC-008`](../plugins/localization.md#loc-008) | Too many relocalizations |
| [`LOC-009`](../plugins/localization.md#loc-009) | `map → odom` jumped |
| [`IMU-001`](../plugins/imu.md#imu-001) | NaN or Inf in the readings |
| [`IMU-002`](../plugins/imu.md#imu-002) | Reading stuck |
| [`IMU-003`](../plugins/imu.md#imu-003) | Implausible magnitude |
| [`IMU-004`](../plugins/imu.md#imu-004) | Frame id changed |
| [`IMU-005`](../plugins/imu.md#imu-005) | Wrong acceleration at rest |
| [`IMU-006`](../plugins/imu.md#imu-006) | Gravity mode changed |
| [`IMU-007`](../plugins/imu.md#imu-007) | Orientation quaternion not normalized |
| [`IMU-008`](../plugins/imu.md#imu-008) | Covariance left at zero |
| [`IMU-009`](../plugins/imu.md#imu-009) | Axis clipped at its range limit |
| [`IMU-010`](../plugins/imu.md#imu-010) | Gyro bias |
| [`IMU-011`](../plugins/imu.md#imu-011) | Stamp gaps or jitter |
| [`GNSS-001`](../plugins/gnss.md#gnss-001) | No fix |
| [`GNSS-002`](../plugins/gnss.md#gnss-002) | Position uncertainty too high |
| [`GNSS-003`](../plugins/gnss.md#gnss-003) | Fix dropping out |
| [`GNSS-004`](../plugins/gnss.md#gnss-004) | Implausible position jump |
| [`GNSS-005`](../plugins/gnss.md#gnss-005) | Fix stuck |
| [`GNSS-006`](../plugins/gnss.md#gnss-006) | NaN or Inf in the position |
| [`GNSS-007`](../plugins/gnss.md#gnss-007) | Frame id changed |
| [`IMG-001`](../plugins/image-stream.md#img-001) | Empty payload |
| [`IMG-002`](../plugins/image-stream.md#img-002) | Malformed frame |
| [`IMG-003`](../plugins/image-stream.md#img-003) | Frame stuck |
| [`IMG-004`](../plugins/image-stream.md#img-004) | Frame size dropped |
| [`IMG-005`](../plugins/image-stream.md#img-005) | Frame id changed |
| [`SENS-001`](../plugins/point-stream.md#sens-001) | Invalid readings |
| [`SENS-002`](../plugins/point-stream.md#sens-002) | Readings stuck |
| [`SENS-003`](../plugins/point-stream.md#sens-003) | All-zero scan |
| [`SENS-004`](../plugins/point-stream.md#sens-004) | Ranges outside the sensor's limits |
| [`SENS-005`](../plugins/point-stream.md#sens-005) | Frame id changed |
| [`SENS-006`](../plugins/point-stream.md#sens-006) | Empty point cloud |
| [`SENS-007`](../plugins/point-stream.md#sens-007) | Malformed point cloud |
| [`VEL-001`](../plugins/velocity-command.md#vel-001) | NaN or Inf in a command |
| [`VEL-002`](../plugins/velocity-command.md#vel-002) | Commanded to move, robot isn't moving |
| [`VEL-003`](../plugins/velocity-command.md#vel-003) | Command reversing direction |
| [`VEL-004`](../plugins/velocity-command.md#vel-004) | Turn rate pinned at one value |
| [`MAP-001`](../plugins/costmap.md#map-001) | Empty or malformed grid |
| [`MAP-002`](../plugins/costmap.md#map-002) | Too much of the grid is lethal |
| [`MAP-003`](../plugins/costmap.md#map-003) | Grid is nearly blank |
| [`MAP-004`](../plugins/costmap.md#map-004) | Grid size or resolution changed |
| [`MAP-005`](../plugins/costmap.md#map-005) | Update doesn't fit the grid |
| [`ACT-001`](../plugins/actions.md#act-001) | Low success rate |
| [`ACT-002`](../plugins/actions.md#act-002) | Goals taking too long |
| [`ACT-003`](../plugins/actions.md#act-003) | Action invoked too often |
| [`BT-001`](../plugins/bt-log.md#bt-001) | Recovering too often |
| [`TF-001`](../plugins/tf-watch.md#tf-001) | Frame with more than one parent |
| [`TF-002`](../plugins/tf-watch.md#tf-002) | Declared chain missing |
| [`TF-003`](../plugins/tf-watch.md#tf-003) | Dynamic transforms stopped updating |
| [`LIVE-001`](../core-checks/live.md#live-001) | No publishers |
| [`LIVE-002`](../core-checks/live.md#live-002) | Publisher present, no messages |
| [`LIVE-003`](../core-checks/live.md#live-003) | Topic went stale |
| [`LIVE-004`](../core-checks/live.md#live-004) | Publishing too slowly |
| [`LIVE-005`](../core-checks/live.md#live-005) | Message type not installed |
| [`STAMP-001`](../core-checks/stamp.md#stamp-001) | Stamp is zero |
| [`STAMP-002`](../core-checks/stamp.md#stamp-002) | Stamp ahead of the clock |
| [`STAMP-003`](../core-checks/stamp.md#stamp-003) | Stamp behind the clock |
| [`CHECK-001`](../core-checks/check.md#check-001) | A plugin's check failed |
| [`NODE-001`](../core-checks/node.md#node-001) | Possible CPU starvation |
| [`NODE-002`](../core-checks/node.md#node-002) | Process in a bad state |
| [`NODE-003`](../core-checks/node.md#node-003) | Memory growing |
| [`NODE-004`](../core-checks/node.md#node-004) | CPU usage high |
| [`NODE-005`](../core-checks/node.md#node-005) | Memory usage high |
