"""Best-effort resolution of ROS node identity to an OS process.

There is no portable API mapping a ROS node name to a PID -- DDS
participant/PID linkage is vendor-specific and not exposed generically
through rclpy. This uses two heuristics, in confidence order:

1. Exact: the process's cmdline carries a `-r __node:=<name>` (optionally
   `__ns:=<namespace>`) remap arg -- an unambiguous fully-qualified match.
2. Fallback: some positional (non-flag) argv token's basename equals a
   candidate node's bare name -- catches the common case of a single-
   instance node named after its executable (most of Nav2's C++ servers,
   launched unremapped). Checks every positional token, not just
   `cmdline[0]`: an rclpy node run as a Python script has `cmdline[0]` ==
   the interpreter (`python3`), not anything node-shaped -- the actual
   telling name is `cmdline[1]` (the script path) instead. A node
   launched this way is extremely common (any custom/user node, several
   of Nav2's own Python nodes), so anchoring only on `cmdline[0]` silently
   dropped most non-C++ nodes from resolution entirely.

Anything neither heuristic resolves is not tracked (this isn't a general
system monitor) -- except Testudo's own PID, unconditionally included
("even Testudo") regardless of whether either heuristic happens to
resolve it.

3. Known-launcher: `ros2 bag play`/`ros2 bag record` run their Player/
   Recorder node *inside the `ros2` CLI's own Python process* -- the
   node's name (`rosbag2_player`/`rosbag2_recorder`, hardcoded by
   `rosbag2_transport`) never appears anywhere in argv (`cmdline` is just
   `python3 .../ros2 bag play <path> ...`), so neither heuristic above can
   ever resolve it. Recognized as one narrow, explicit special case --
   these two node names are stable, documented defaults, and `ros2 bag`
   is common enough (including underpinning Testudo's own `replay`
   workflow) to be worth naming directly, unlike guessing at arbitrary
   third-party tools with the same problem.

Known gap, not solved here: a composition container hosts N ROS nodes in
one OS process. Heuristic 1 usually resolves the *container's own* node
identity correctly (it's a real node with its own remap), but the N nodes
it hosts share that one process's CPU/memory reading no matter how
precisely each is labeled -- there is no way to attribute a fraction of
one process's resource usage to one of several nodes sharing it. A
`~/_container/list_nodes` service call could improve the *label* (listing
which nodes a container hosts) but would not change this -- deliberately
left as a documented future enhancement, not built here.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Iterable

import psutil

from testudo.core.node_profiling.cmdline_parse import parse_remapped_node_name


@dataclass(frozen=True)
class ResolvedProcess:
    """One OS process Testudo is tracking, with its best-effort ROS identity."""

    pid: int
    identity: str  # fully-qualified ROS node name if resolved, else f"pid:{pid}"
    process_name: str
    resolved: bool


def resolve_processes(
    node_names: list[str],
    self_pid: int | None = None,
    process_iter: Callable[..., Iterable[object]] | None = None,
) -> list[ResolvedProcess]:
    """Match `node_names` (fully-qualified, from `get_node_names_and_namespaces`) to live OS processes.

    `self_pid` (default `os.getpid()`) is always included, resolved or
    not -- Testudo profiling itself must not depend on either heuristic
    succeeding. `process_iter` is injectable (default `None`, resolved to
    `psutil.process_iter` inside the function body rather than bound as a
    literal default -- a default bound at function-definition time would
    permanently capture the *original* `psutil.process_iter`, immune to a
    test's `monkeypatch.setattr(psutil, "process_iter", ...)` on the
    module attribute) so this is unit-testable with a synthetic process
    list, no live processes required.
    """
    self_pid = os.getpid() if self_pid is None else self_pid
    if process_iter is None:
        process_iter = psutil.process_iter
    remaining = set(node_names)
    resolved: dict[int, ResolvedProcess] = {}
    processes = _live_processes(process_iter)

    for pid, cmdline, name in processes:
        fqn = parse_remapped_node_name(cmdline)
        if fqn is not None and fqn in remaining:
            resolved[pid] = ResolvedProcess(pid=pid, identity=fqn, process_name=name or _first_basename(cmdline), resolved=True)
            remaining.discard(fqn)

    if remaining:
        for pid, cmdline, name in processes:
            if pid in resolved or not remaining:
                continue
            match, matched_basename = _bare_name_match(cmdline, remaining)
            if match is not None:
                resolved[pid] = ResolvedProcess(pid=pid, identity=match, process_name=name or matched_basename, resolved=True)
                remaining.discard(match)

    if self_pid not in resolved:
        self_name = next((name or _first_basename(cmdline) for pid, cmdline, name in processes if pid == self_pid), "testudo")
        resolved[self_pid] = ResolvedProcess(pid=self_pid, identity=f"pid:{self_pid}", process_name=self_name, resolved=False)

    return list(resolved.values())


def _live_processes(process_iter: Callable[..., Iterable[object]]) -> list[tuple[int, list[str], str]]:
    """(pid, cmdline, process name) for every process still alive as of this call.

    A process that exits between enumeration and info access is skipped,
    not retried -- the next rediscovery pass picks up whatever is running
    then, the same "best effort, next cycle catches up" stance
    `core/maintenance.py` already takes for topic discovery.
    """
    result = []
    for proc in process_iter(["pid", "cmdline", "name"]):
        try:
            info = proc.info
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        result.append((info["pid"], info.get("cmdline") or [], info.get("name") or ""))
    return result


def _first_basename(cmdline: list[str]) -> str:
    return os.path.basename(cmdline[0]) if cmdline else ""


def _positional_basenames(cmdline: list[str]) -> list[str]:
    """Every non-flag argv token's basename, in order.

    Covers both a direct binary invocation (`cmdline[0]` is the node's own
    executable, matched first) and an interpreter-run script (`cmdline[0]`
    is `python3`; the actual script -- often named after the node -- is
    `cmdline[1]`) without needing to special-case which interpreters exist.
    """
    return [os.path.basename(token) for token in cmdline if token and not token.startswith("-")]


#: `ros2 bag play <bag>` / `ros2 bag record ...` -> the well-known,
#: hardcoded node name `rosbag2_transport` gives its Player/Recorder.
_ROS2_BAG_VERB_NODE_NAMES = {"play": "rosbag2_player", "record": "rosbag2_recorder"}


def _ros2_bag_node_name(cmdline: list[str]) -> str | None:
    """"rosbag2_player"/"rosbag2_recorder" if `cmdline` looks like `ros2 bag play|record ...`, else None.

    The node's name isn't in argv at all for this invocation (see this
    module's docstring, heuristic 3) -- this recovers it from the `ros2
    bag <verb>` shape instead of a basename. `tokens.index(...)` requiring
    "bag" strictly after "ros2" (not just "both present somewhere") rules
    out an unrelated process that merely happens to have "ros2" and "bag"
    as separate, unconnected arguments.
    """
    tokens = _positional_basenames(cmdline)
    if "ros2" not in tokens or "bag" not in tokens:
        return None
    bag_index = tokens.index("bag")
    if tokens.index("ros2") >= bag_index:
        return None
    verbs_seen = tokens[bag_index + 1 :]
    for verb, node_name in _ROS2_BAG_VERB_NODE_NAMES.items():
        if verb in verbs_seen:
            return node_name
    return None


def _candidate_basenames(cmdline: list[str]) -> list[str]:
    """`_positional_basenames`, plus a recognized `ros2 bag` node name up front (highest priority) if applicable."""
    known = _ros2_bag_node_name(cmdline)
    basenames = _positional_basenames(cmdline)
    return [known] + basenames if known is not None else basenames


def _bare_name_match(cmdline: list[str], remaining: set[str]) -> tuple[str | None, str | None]:
    """The first `remaining` fully-qualified name whose bare part matches one of `cmdline`'s candidate basenames.

    Tries each basename both as-is and with a trailing file extension
    stripped (`controller_server.py` -> `controller_server`) -- a node run
    directly as a script (`python3 controller_server.py`, common for an
    unpackaged/dev node, or a light-path plugin's own test harness) keeps
    its `.py` suffix in argv, which a compiled/console-script executable's
    extensionless name never has to begin with.
    """
    for basename in _candidate_basenames(cmdline):
        stem = os.path.splitext(basename)[0]
        match = next((fqn for fqn in remaining if fqn.rsplit("/", 1)[-1] in (basename, stem)), None)
        if match is not None:
            return match, basename
    return None, None
