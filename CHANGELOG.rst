^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
Changelog for package testudo
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

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
