"""Periodic node<->process rediscovery for `NodeProfiler`.

Mirrors `core/maintenance.py`'s module-function style and its two-seam
thread-ownership split: `rediscover()` calls `node.get_node_names_and_
namespaces()` (an rclpy graph call), so it must run on whichever thread
spins `profiler._node` -- in practice, a timer `NodeProfiler.start()`
creates on that node, so it always does. It owns and replaces the
pid->identity mapping wholesale (one atomic reference swap, no lock,
matching `subscription_manager.py`'s own "plain mutation, safe from any
thread" pattern) -- `NodeProfiler.tick()` (on a different thread in
`watch`) only ever reads that mapping, never mutates it.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from testudo.core.discovery import fully_qualified_node_name
from testudo.core.node_profiling.process_match import resolve_processes

if TYPE_CHECKING:
    from testudo.core.node_profiling.profiler import NodeProfiler


def rediscover(profiler: "NodeProfiler") -> None:
    """Re-run node<->process resolution and publish a fresh mapping for `tick()` to read.

    Also sweeps the profiler's cached `psutil.Process` handles for any PID
    no longer resolved, so a process that has exited doesn't keep a stale
    handle (and its now-meaningless `cpu_percent` baseline) around.
    """
    node_names = [
        fully_qualified_node_name(namespace, name) for name, namespace in profiler._node.get_node_names_and_namespaces()
    ]
    resolved = resolve_processes(node_names, self_pid=profiler._self_pid)
    resolved = [r for r in resolved if r.pid == profiler._self_pid or not _is_excluded(profiler, r.identity)]
    profiler._mapping = {r.pid: r for r in resolved}
    profiler._sampler.prune(set(profiler._mapping))


def _is_excluded(profiler: "NodeProfiler", identity: str) -> bool:
    return any(rule.matches(identity) for rule in profiler._config.exclude_nodes)
