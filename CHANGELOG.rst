^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
Changelog for package testudo
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Forthcoming
-----------
* GNSS: without a fix, only GNSS-001 is raised; the placeholder σ, the repeated position and a window of nothing but dropouts no longer add GNSS-002, -005 and -003.
* TF: a ``/tf`` that never publishes is idle while the whole tree comes from ``/tf_static`` (fixed joints only, no localization running), instead of LIVE-002.
* IMU: a stream carrying only gyro or only acceleration (a RealSense without ``unite_imu_method``) says so, and a gyro-only stream no longer reads as gravity removed.
* An undeclared topic a plugin checks that has never published is idle, as an unchecked one already was, instead of LIVE-002 (GLIM's ``/glim_ros/grid``).
* Node profiling: a process launched with only ``__ns:=`` is matched to the one unmatched node in that namespace (a RealSense driver), and an executable named like its node plus ``node`` matches (GLIM's ``glim_rosnode``).
* Testudo uses rclpy's events executor where there is one (Jazzy and newer): on a load like the first real robot's (~4,700 msg/s) it takes about a fifth of a core instead of a whole one, and keeps up with its topics. ``TESTUDO_EXECUTOR=single`` falls back to the older executor.
* A topic's rate is averaged over at least its last second of messages, not only its last 20: a 100 Hz topic read up to 12% fast.
* After a stack closes, its topics no longer come back as "no publishers" rows when only Testudo's own just-destroyed subscriptions still list them, and a topic with no publishers is forgotten once it leaves the graph.
* IMU: a stamp gap Testudo caused itself, by falling behind on a loaded host, is no longer IMU-011: the message after it arrives later than usual after its stamp, having waited in Testudo's own queue.
* Localization: LOC-003 judges only growth, never a shrinking covariance, as a trend fitted over the last 10 s instead of across the last 10 messages, so a filter's rise between two GNSS fixes isn't growth.
* GNSS: the default ``position_covariance`` level suits an ordinary receiver (warn past 25 m², a σ of 5 m per axis) instead of RTK (σ 1 cm); an RTK receiver tightens it in config.
* Node profiling: ``node_profiling.nodes`` gives a node its own levels, e.g. a mapper whose memory grows with its map.
* Testudo subscribes with a history of 100 messages (10 for images and point clouds): a pause of a few tens of ms overflowed the old 10 at 400 Hz, and on a slower host the 400 Hz topics read 11% low and showed gaps that weren't there.
* Maps: a grid that keeps changing size is a map being built, so only its first resize is MAP-004; a resolution change still always is.
* TF: a ``/tf_static`` checked on its own, with no ``/tf`` on the graph or in the bag, counts its transforms as static, instead of as dynamic edges that went stale (TF-003) after 10 s.
* Docs: the GNSS clip shows the accuracy collapsing (GNSS-002) before the fix is lost (GNSS-001); the dashboard media were recorded again.

1.0.0 (2026-11-19)
------------------
* The first public release.
* Watches a running ROS 2 stack from the outside, with no changes to its nodes and no config required.
* Gives each topic the cheapest monitoring that can judge it: full content checks, vitals, presence or excluded.
* Tells quiet topics (latched, event-driven, idle inputs) apart from dead ones, and follows ``use_sim_time``, lifecycle states and stacks that relaunch.
* Plugins for localization, IMU, GNSS, cameras, lidars and point clouds, velocity commands, maps and costmaps, actions, Nav2's behavior tree log and TF.
* Core checks for liveness, rates (including those Nav2's parameters ask for), header stamps and per-node CPU, memory and GPU use.
* Every problem has a stable code with its own documentation entry.
* Plugins can be added from a single file or from an installed package.
* ``testudo watch``: a live terminal dashboard, or ``--headless`` for robots, launch files and CI.
* ``testudo replay``: a report on a recorded bag with an exit code, or ``--watch`` to scrub through it in the dashboard.
* ``testudo config init``: a starting config written from the running stack; ``testudo plugins``: the checks available.
* Results published as standard ``diagnostic_msgs/DiagnosticArray`` topics, including a history of faults on ``/testudo/faults``.
* Contributors: mlisi1
