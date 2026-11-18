# Plugins

Each plugin understands one kind of topic and judges its content. They turn on by themselves when a matching topic appears, except cameras and point clouds, which you declare because subscribing to them is expensive.

<div class="grid cards" markdown>

-   :material-crosshairs-gps:{ .lg .middle } **[Localization](localization.md)**

    ---

    Odometry and pose estimates: covariance, jumps and relocalizations.

    `LOC`

-   :material-axis-arrow:{ .lg .middle } **[IMU](imu.md)**

    ---

    Units, gravity, gyro bias, clipped axes and stamp gaps.

    `IMU`

-   :material-satellite-variant:{ .lg .middle } **[GNSS](gnss.md)**

    ---

    Fix status, claimed accuracy, dropouts and position jumps.

    `GNSS`

-   :material-camera:{ .lg .middle } **[Image stream](image-stream.md)**

    ---

    Empty, malformed, frozen or suddenly tiny camera frames.

    `IMG`

-   :material-radar:{ .lg .middle } **[Point stream](point-stream.md)**

    ---

    Invalid, stuck or malformed 2D and 3D lidar data.

    `SENS`

-   :material-speedometer:{ .lg .middle } **[Velocity command](velocity-command.md)**

    ---

    Invalid commands, a robot that doesn't respond, oscillation.

    `VEL`

-   :material-map:{ .lg .middle } **[Costmap](costmap.md)**

    ---

    Lethal and unknown share of maps and costmaps, drawn in the terminal.

    `MAP`

-   :material-flag-checkered:{ .lg .middle } **[Actions](actions.md)**

    ---

    How the goals of any action server end, and how long they take.

    `ACT`

-   :material-file-tree:{ .lg .middle } **[Behavior tree log](bt-log.md)**

    ---

    What Nav2's tree is doing, and how often it recovers.

    `BT`

-   :material-family-tree:{ .lg .middle } **[TF watch](tf-watch.md)**

    ---

    Multiple parents, missing chains and stopped transforms.

    `TF`

</div>

Run `testudo plugins` to list what's installed, including your own. To add a check, see [Write a plugin](../developers/plugin-guide.md).
