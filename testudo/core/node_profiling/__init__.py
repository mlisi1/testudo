"""Process-level CPU/memory/GPU profiling for every resolvable ROS node, plus Testudo itself.

A separate subsystem from the plugin/topic pipeline in `testudo.core` and
`testudo.plugins` -- see this package's module docstrings for why: node
profiling has no ROS message type to key discovery on, and no topic
subscription to hang a `CheckPlugin` off of.
"""
