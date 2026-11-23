# Command line

ROS arguments go after `--ros-args`, for example `testudo watch --ros-args -p use_sim_time:=true`.

`testudo replay` exits with `0` when the bag ends OK, `1` on WARN and `2` on anything worse.

`TESTUDO_EXECUTOR=single` makes `testudo watch` use rclpy's older single-threaded executor instead of the events executor. See [what monitoring costs](../concepts/tiers.md#what-monitoring-costs).

--8<-- "cli.md"
