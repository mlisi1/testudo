"""Costmap and map content-check plugin for nav_msgs/msg/OccupancyGrid.

An empty or malformed grid, too much of it lethal or unknown, and a size or
resolution that changes. Obstacles that stay in a costmap are not a fault:
not clearing them can be what the user configured.

A costmap may publish in full once and then only incremental updates, so
the plugin follows `<topic>_updates` whenever that topic exists. The status
carries a picture of the grid for the Detail Panel. No robot is drawn on it:
which topic holds the right pose isn't something Testudo can know.
"""
from __future__ import annotations

import math
import time
from typing import Any

import numpy as np

from testudo.plugins.base import ActiveCodes, CheckPlugin, CheckStatus, Severity, SeverityLevels, ThresholdZone, evaluate_levels
from testudo.plugins.builtin.costmap_grid import CLASS_NAMES, PALETTE, GridAnalysis, analyse

_LABEL = "map"
_UPDATES = "updates"
#: Counting cells of a large grid isn't free: at most this often...
_ANALYSIS_INTERVAL_SECONDS = 1.0
#: ...and never taking more than one part in this many of the spin thread's time.
_ANALYSIS_DUTY_DIVISOR = 10.0
#: How long a one-off event stays an active code.
_EVENT_HOLD_SECONDS = 10.0
#: The full topic may stay silent while updates keep arriving this recently.
_UPDATES_KEEP_ALIVE_SECONDS = 10.0


def _malformed(msg: Any) -> str | None:
    """What makes a full grid unusable, or None."""
    info = msg.info
    if info.width == 0 or info.height == 0:
        return f"empty grid ({info.width} x {info.height} cells)"
    if not math.isfinite(info.resolution) or info.resolution <= 0.0:
        return f"invalid resolution {info.resolution}"
    if len(msg.data) != info.width * info.height:
        return f"{len(msg.data)} cells for a {info.width} x {info.height} grid"
    return None


class CostmapPlugin(CheckPlugin):
    """Empty grid, lethal and unknown share, size change and update checks for an occupancy grid."""

    def __init__(
        self,
        thresholds: dict[str, ThresholdZone] | None = None,
        related_topics: dict[str, str] | None = None,
        *,
        levels: dict[str, SeverityLevels] | None = None,
    ) -> None:
        super().__init__(thresholds, related_topics, levels=levels)
        self._now = 0.0
        self._cells: np.ndarray | None = None
        self._geometry: tuple[int, int, float] | None = None
        self._frame_id = ""
        self._problem: str | None = None
        self._resized: tuple[float, str] | None = None
        self._bad_update: tuple[float, str] | None = None
        self._last_update: float | None = None
        self._analysis: GridAnalysis | None = None
        self._analysed_at: float | None = None
        self._analysis_interval = _ANALYSIS_INTERVAL_SECONDS
        self._stale_analysis = False

    @classmethod
    def msg_types(cls) -> tuple[str, ...]:
        return ("nav_msgs/msg/OccupancyGrid",)

    @classmethod
    def default_thresholds(cls) -> dict[str, ThresholdZone]:
        return {}

    @classmethod
    def default_levels(cls) -> dict[str, SeverityLevels]:
        # A static map is mostly unknown around the building, so only a nearly blank one counts.
        return {
            "lethal_ratio": SeverityLevels(warn=0.6, error=0.9),
            "unknown_ratio": SeverityLevels(warn=0.9, error=0.999),
        }

    @classmethod
    def default_related_topics(cls, topic_name: str) -> dict[str, str]:
        return {_UPDATES: f"{topic_name}_updates"}

    def silence_expected(self, msg_type: str) -> bool:
        return self._last_update is not None and self._now - self._last_update < _UPDATES_KEEP_ALIVE_SECONDS

    def on_tick(self, now_seconds: float) -> None:
        self._now = now_seconds
        self._refresh_analysis()

    def on_message(self, topic: str, msg: Any) -> None:
        if topic == self.related_topics.get(_UPDATES):
            self._on_update(msg)
        else:
            self._on_grid(msg)
        self._refresh_analysis()

    def _on_grid(self, msg: Any) -> None:
        self._problem = _malformed(msg)
        if self._problem is not None:
            return
        info = msg.info
        geometry = (info.width, info.height, info.resolution)
        if self._geometry is not None and geometry != self._geometry:
            before = f"{self._geometry[0]} x {self._geometry[1]} at {self._geometry[2]:.3g} m"
            self._resized = (self._now, f"grid changed from {before} to {info.width} x {info.height} at {info.resolution:.3g} m")
        self._geometry = geometry
        self._frame_id = msg.header.frame_id
        self._cells = np.array(msg.data, dtype=np.int8).reshape(info.height, info.width)
        self._stale_analysis = True

    def _on_update(self, msg: Any) -> None:
        if self._cells is None:
            return  # nothing to apply it to until the first full grid
        self._last_update = self._now
        height, width = self._cells.shape
        fits = msg.x >= 0 and msg.y >= 0 and msg.x + msg.width <= width and msg.y + msg.height <= height
        if not fits or len(msg.data) != msg.width * msg.height:
            self._bad_update = (self._now, f"update of {msg.width} x {msg.height} at ({msg.x}, {msg.y}) doesn't fit the {width} x {height} grid")
            return
        patch = np.array(msg.data, dtype=np.int8).reshape(msg.height, msg.width)
        self._cells[msg.y : msg.y + msg.height, msg.x : msg.x + msg.width] = patch
        self._stale_analysis = True

    def _refresh_analysis(self) -> None:
        if self._cells is None or not self._stale_analysis:
            return
        if self._analysed_at is not None and 0.0 <= self._now - self._analysed_at < self._analysis_interval:
            return
        started = time.perf_counter()
        self._analysis = analyse(self._cells)
        spent = time.perf_counter() - started
        self._analysis_interval = max(_ANALYSIS_INTERVAL_SECONDS, spent * _ANALYSIS_DUTY_DIVISOR)
        self._analysed_at = self._now
        self._stale_analysis = False

    def _held(self, event: tuple[float, str] | None) -> str | None:
        """An event's text while it is recent enough to stay active."""
        return event[1] if event is not None and self._now - event[0] < _EVENT_HOLD_SECONDS else None

    def _findings(self) -> list[tuple[str, int, str]]:
        """The active problems, as (code, severity, text)."""
        findings: list[tuple[str, int, str]] = []
        if self._problem is not None:
            findings.append(("MAP-001", Severity.ERROR, self._problem))
        if self._analysis is not None:
            lethal, unknown = self._analysis.lethal_ratio, self._analysis.unknown_ratio
            severity = evaluate_levels(lethal, self.levels.get("lethal_ratio"))
            if severity != Severity.OK:
                findings.append(("MAP-002", severity, f"{lethal:.0%} of the known cells are lethal"))
            severity = evaluate_levels(unknown, self.levels.get("unknown_ratio"))
            if severity != Severity.OK:
                findings.append(("MAP-003", severity, f"{unknown:.1%} of the grid is unknown"))
        resized = self._held(self._resized)
        if resized is not None:
            findings.append(("MAP-004", Severity.WARN, resized))
        bad_update = self._held(self._bad_update)
        if bad_update is not None:
            findings.append(("MAP-005", Severity.WARN, bad_update))
        return findings

    def get_status(self) -> CheckStatus:
        findings = self._findings()
        codes = ActiveCodes()
        for code, severity, text in findings:
            codes.add(code, text, severity)
        analysis = self._analysis
        if analysis is None and not findings:
            return CheckStatus(severity=Severity.OK, label=_LABEL, message="no grid received yet")
        summary = f"{analysis.lethal_ratio:.1%} lethal, {analysis.unknown_ratio:.1%} unknown" if analysis is not None else ""
        return CheckStatus(
            severity=max((severity for _code, severity, _text in findings), default=Severity.OK),
            label=_LABEL,
            message="; ".join(text for _code, _severity, text in findings) or summary,
            values=self._values(analysis),
            topic_panel_column="Lethal",
            topic_panel_value=f"{analysis.lethal_ratio:.1%}" if analysis is not None else "",
            codes=codes.messages,
            code_severities=codes.severities,
            metrics=self._metrics(analysis),
            picture=analysis.picture if analysis is not None else None,
        )

    def _values(self, analysis: GridAnalysis | None) -> dict[str, str]:
        """What the Detail Panel shows next to the picture."""
        if analysis is None or self._geometry is None:
            return {}
        width, height, resolution = self._geometry
        values = {
            "size": f"{width} x {height} cells",
            "extent": f"{width * resolution:.1f} x {height * resolution:.1f} m",
            "resolution": f"{resolution:.3g} m",
            "lethal": f"{analysis.lethal_ratio:.1%}",
            "unknown": f"{analysis.unknown_ratio:.1%}",
            "frame": self._frame_id,
        }
        if self._last_update is not None:
            values["last_update"] = f"{self._now - self._last_update:.1f} s ago"
        legend = "  ".join(f"[{color}]■[/] {name}" for color, name in zip(PALETTE, CLASS_NAMES))
        values["legend_more"] = legend
        return values

    def _metrics(self, analysis: GridAnalysis | None) -> dict[str, float | str]:
        """The published numbers."""
        if analysis is None or self._geometry is None:
            return {}
        width, height, resolution = self._geometry
        return {
            "width_cells": width,
            "height_cells": height,
            "resolution_m": resolution,
            "lethal_ratio": analysis.lethal_ratio,
            "unknown_ratio": analysis.unknown_ratio,
            "frame_id": self._frame_id,
        }
