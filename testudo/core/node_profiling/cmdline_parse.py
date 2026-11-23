"""Pure parsing of a process's ROS 2 remap arguments from its cmdline.

No psutil/rclpy imports here -- string-in/string-out, fully unit-testable
without a live process or ROS graph. See `process_match.py` for how this
fits into the actual node<->process resolution heuristic.
"""
from __future__ import annotations

from testudo.core.discovery import fully_qualified_node_name

#: `--ros-args -r __node:=name -r __ns:=/ns` -- the remap flags rclpy/rclcpp
#: accept for overriding a node's default name/namespace at launch. Not
#: every node passes these (most keep their hardcoded default name), but
#: when present this is an exact, unambiguous identity -- see
#: `process_match.py` for the fallback heuristic used when it's absent.
_NODE_NAME_REMAP_PREFIX = "__node:="
_NAMESPACE_REMAP_PREFIX = "__ns:="


def parse_remapped_node_name(cmdline: list[str]) -> str | None:
    """The fully-qualified node name remapped via `-r __node:=...`/`-r __ns:=...`, or None.

    `cmdline` is a process's argv as psutil reports it (`Process.cmdline()`
    / `Process.info["cmdline"]`). A remap can appear as `-r __node:=X` (two
    argv tokens, the common case from `ros2 launch`) or concatenated as
    `-r__node:=X`/`--remap=__node:=X` -- every token is scanned for the
    prefix rather than assuming one fixed argv shape.
    """
    node_name, namespace = _remaps(cmdline)
    if not node_name:
        return None
    return fully_qualified_node_name(namespace or "", node_name)


def parse_remapped_namespace(cmdline: list[str]) -> str | None:
    """The namespace a process is launched into with `-r __ns:=...` and no `__node:=`, as `/ns`; else None.

    Such a node keeps the name its code gives it (a RealSense driver launched
    as `realsense2_camera_node --ros-args -r __ns:=/front_camera` is
    `/front_camera/camera`), so only its namespace is known from argv.
    """
    node_name, namespace = _remaps(cmdline)
    if node_name or namespace is None:
        return None
    return "/" + namespace.strip("/")


def _remaps(cmdline: list[str]) -> tuple[str | None, str | None]:
    """The `__node:=` and `__ns:=` values in `cmdline`, each None when absent (the last one wins)."""
    node_name: str | None = None
    namespace: str | None = None
    for token in cmdline:
        value = _remap_value(token)
        if value is None:
            continue
        if value.startswith(_NODE_NAME_REMAP_PREFIX):
            node_name = value[len(_NODE_NAME_REMAP_PREFIX) :]
        elif value.startswith(_NAMESPACE_REMAP_PREFIX):
            namespace = value[len(_NAMESPACE_REMAP_PREFIX) :]
    return node_name, namespace


def _remap_value(token: str) -> str | None:
    """Strip a leading `-r`/`--remap=` flag prefix a remap token may carry, if any.

    Handles `-r__node:=X` (flag and value concatenated) and
    `--remap=__node:=X` by stripping the flag; a bare `__node:=X` token
    (the flag as a separate preceding argv entry, e.g. `-r __node:=X`)
    already matches the prefix check unchanged, so no flag-stripping is
    needed for that -- by far the most common shape from `ros2 launch`.
    """
    if token.startswith("--remap="):
        token = token[len("--remap=") :]
    elif token.startswith("-r") and len(token) > 2 and token[2] == "_":
        token = token[2:]
    if token.startswith(_NODE_NAME_REMAP_PREFIX) or token.startswith(_NAMESPACE_REMAP_PREFIX):
        return token
    return None
