# Changelog

## Unreleased

### Fixes from the first real-robot bags

- GNSS: without a fix, only GNSS-001 is raised; the placeholder σ, the repeated position and a window of nothing but dropouts no longer add GNSS-002, -005 and -003.
- TF: a `/tf` that never publishes is idle while the whole tree comes from `/tf_static` (fixed joints only, no localization running), instead of LIVE-002.
- IMU: a stream carrying only gyro or only acceleration (a RealSense without `unite_imu_method`) says so, and a gyro-only stream no longer reads as gravity removed.
- Node profiling: a process launched with only `__ns:=` is matched to the one unmatched node in that namespace (a RealSense driver), and an executable named like its node plus `node` matches (GLIM's `glim_rosnode`).

### Docs

- The GNSS clip shows the accuracy collapsing (GNSS-002) before the fix is lost (GNSS-001); the dashboard media were recorded again.

## 1.0.0 (2026-11-19)

The first public release.

### Monitoring

- Watches a running ROS 2 stack from the outside, with no changes to its nodes and no config required.
- Gives each topic the cheapest monitoring that can judge it: full content checks, vitals, presence or excluded.
- Tells quiet topics (latched, event-driven, idle inputs) apart from dead ones.
- Follows `use_sim_time`, lifecycle states and stacks that close and relaunch.

### Checks

- Plugins for localization, IMU, GNSS, cameras, lidars and point clouds, velocity commands, maps and costmaps, actions, Nav2's behavior tree log and TF.
- Core checks for liveness, rates (including those Nav2's parameters ask for), header stamps and per-node CPU, memory and GPU use.
- Every problem has a stable code with its own documentation entry.
- Plugins can be added from a single file or from an installed package.

### Tools

- `testudo watch`: a live terminal dashboard, or `--headless` for robots, launch files and CI.
- `testudo replay`: a report on a recorded bag with an exit code, or `--watch` to scrub through it in the dashboard.
- `testudo config init`: a starting config written from the running stack.
- `testudo plugins`: the checks available and what they cover.
- Results published as standard `diagnostic_msgs/DiagnosticArray` topics, including a history of faults on `/testudo/faults`.
- Quick actions and options in the dashboard, saved to your config without touching the rest of the file.
