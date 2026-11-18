# Plugins

Each plugin checks one kind of topic. A topic gets its plugin's checks automatically, except cameras and point clouds, which must be declared.

| Plugin | Checks | Codes |
|---|---|---|
| [Localization](localization.md) | Odometry and pose estimates | `LOC` |
| [IMU](imu.md) | Inertial sensors | `IMU` |
| [GNSS](gnss.md) | Satellite fixes | `GNSS` |
| [Image stream](image-stream.md) | Cameras | `IMG` |
| [Point stream](point-stream.md) | 2D and 3D lidar | `SENS` |
| [Velocity command](velocity-command.md) | What the controller commands | `VEL` |
| [Costmap](costmap.md) | Maps and costmaps | `MAP` |
| [Actions](actions.md) | Goal outcomes of any action | `ACT` |
| [Behavior tree log](bt-log.md) | Nav2's behavior tree | `BT` |
| [TF watch](tf-watch.md) | The transform tree | `TF` |

Run `testudo plugins` to list what's installed, including your own. To add a check, see the [plugin guide](../developers/plugin-guide.md).
