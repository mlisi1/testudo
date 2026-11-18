"""The `map → odom` row: how hard localization is correcting the robot's position.

`map → odom` is the correction a global localizer applies on top of the
odometry, so a sudden change in it is the localizer jumping, even on a stack
that publishes no pose topic. No single localization topic owns it, so it's
watched once on `/tf` and shown as a row of its own in the Localization
category (see `TFWatchPlugin.derived_rows`).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from testudo.plugins.base import ActiveCodes, CheckStatus, Severity, SeverityLevels, evaluate_levels
from testudo.plugins.builtin.localization_jumps import Jump, JumpTracker, quaternion_yaw

#: Normal corrections are centimeters; half a meter at once is the localizer jumping.
DEFAULT_MAP_ODOM_LEVELS = {
    "map_odom_jump_m": SeverityLevels(warn=0.5, error=5.0),
    "map_odom_jump_rad": SeverityLevels(warn=0.5),
}

#: The row's message type and the plugin whose output topic it's published on.
ROW_MSG_TYPE = "geometry_msgs/msg/TransformStamped"
ROW_PLUGIN_GROUP = "localization"


@dataclass(frozen=True)
class DerivedRow:
    """A report row a plugin works out from its topic, beyond that topic's own."""

    name: str
    msg_type: str
    plugin_group: str
    status: CheckStatus


@dataclass
class _Edge:
    tracker: JumpTracker = field(default_factory=JumpTracker)
    correction: tuple[float, float] = (0.0, 0.0)
    largest_m: float = 0.0


def _is_map_odom(parent: str, child: str) -> bool:
    """True for `map → odom`, namespaced or not (`robot1/map → robot1/odom`)."""
    return child.rsplit("/", 1)[-1] == "odom" and parent.rsplit("/", 1)[-1] == "map"


class MapOdomWatch:
    """Follows every `map → odom` transform seen on `/tf`, one row per robot namespace."""

    def __init__(self) -> None:
        self._edges: dict[tuple[str, str], _Edge] = {}

    def observe(self, transform: Any, now: float, levels: dict[str, SeverityLevels]) -> None:
        """Take one geometry_msgs TransformStamped; anything but `map → odom` is ignored."""
        parent, child = transform.header.frame_id, transform.child_frame_id
        if not _is_map_odom(parent, child):
            return
        edge = self._edges.setdefault((parent, child), _Edge())
        translation = transform.transform.translation
        position = (float(translation.x), float(translation.y), float(translation.z))
        distance, turned = edge.tracker.step(position, quaternion_yaw(transform.transform.rotation))
        edge.correction = (distance, turned)
        edge.largest_m = max(edge.largest_m, distance)
        severity = max(
            evaluate_levels(distance, levels.get("map_odom_jump_m")),
            evaluate_levels(turned, levels.get("map_odom_jump_rad")),
        )
        if severity != Severity.OK:
            edge.tracker.record(Jump(now, distance, turned, severity))

    def rows(self, now: float) -> list[DerivedRow]:
        """One row per `map → odom` seen so far."""
        return [
            DerivedRow(f"{parent} → {child}", ROW_MSG_TYPE, ROW_PLUGIN_GROUP, _status(edge, now))
            for (parent, child), edge in sorted(self._edges.items())
        ]


def _status(edge: _Edge, now: float) -> CheckStatus:
    """The row's status: LOC-009 while a jump is held, with the corrections so far as values."""
    codes = ActiveCodes()
    held = edge.tracker.held(now)
    severity = Severity.OK
    message = "map → odom steady"
    if held is not None:
        severity = held.severity
        message = f"map → odom jumped {held.distance_m:.2f} m, {math.degrees(held.yaw_rad):.0f}°"
        codes.add("LOC-009", message, severity)
    distance, turned = edge.correction
    values = {
        "last_correction": f"{distance:.3f} m, {math.degrees(turned):.1f}°",
        "largest_correction": f"{edge.largest_m:.3f} m",
        "jumps": str(edge.tracker.count),
    }
    return CheckStatus(
        severity=severity,
        label="localization",
        message=message,
        values=values,
        # The Localization category's extra column: nothing to show here.
        topic_panel_column="σxy",
        topic_panel_value="[dim]–[/dim]",
        codes=codes.messages,
        code_severities=codes.severities,
        metrics={
            "last_correction_m": distance,
            "last_correction_rad": turned,
            "largest_correction_m": edge.largest_m,
            "jumps": edge.tracker.count,
        },
    )
