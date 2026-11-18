"""Unit tests for CostmapPlugin -- synthetic grids, no live ROS graph."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from nav_msgs.msg import OccupancyGrid

from testudo.plugins.base import Severity
from testudo.plugins.builtin.costmap import CostmapPlugin
from testudo.plugins.builtin.costmap_grid import MAX_PICTURE_HEIGHT, MAX_PICTURE_WIDTH, analyse

_RELATED = {"updates": "/costmap_updates"}


def _grid(width: int = 10, height: int = 10, fill: int = 0, resolution: float = 0.05) -> OccupancyGrid:
    msg = OccupancyGrid()
    msg.header.frame_id = "map"
    msg.info.width, msg.info.height, msg.info.resolution = width, height, resolution
    msg.data = [fill] * (width * height)
    return msg


@dataclass
class _Update:
    x: int = 0
    y: int = 0
    width: int = 1
    height: int = 1
    data: list[int] = field(default_factory=lambda: [100])


def _status_of(msg: OccupancyGrid):
    plugin = CostmapPlugin()
    plugin.on_message("/costmap", msg)
    return plugin.get_status()


def test_no_grid_yet_is_ok() -> None:
    status = CostmapPlugin().get_status()
    assert status.severity == Severity.OK
    assert "no grid" in status.message


def test_updates_follow_the_topic_by_convention() -> None:
    assert CostmapPlugin.default_related_topics("/global_costmap/costmap") == {"updates": "/global_costmap/costmap_updates"}


def test_a_healthy_grid_is_ok() -> None:
    status = _status_of(_grid())
    assert status.codes == {}
    assert status.metrics["lethal_ratio"] == 0.0
    assert status.values["extent"] == "0.5 x 0.5 m"
    assert status.topic_panel_column == "Lethal"


def test_an_empty_grid_is_an_error() -> None:
    assert _status_of(_grid(0, 0)).code_severities == {"MAP-001": Severity.ERROR}


def test_a_grid_with_missing_cells_is_an_error() -> None:
    msg = _grid()
    msg.data = [0] * 7
    assert "7 cells for a 10 x 10 grid" in _status_of(msg).codes["MAP-001"]


def test_an_invalid_resolution_is_an_error() -> None:
    assert "MAP-001" in _status_of(_grid(resolution=0.0)).codes


def test_a_mostly_lethal_grid_warns_and_an_all_lethal_one_errors() -> None:
    msg = _grid()
    msg.data = [100] * 70 + [0] * 30
    assert _status_of(msg).code_severities == {"MAP-002": Severity.WARN}
    assert _status_of(_grid(fill=100)).code_severities == {"MAP-002": Severity.ERROR}


def test_lethal_share_counts_known_cells_only() -> None:
    msg = _grid()
    msg.data = [-1] * 80 + [100] * 5 + [0] * 15
    assert _status_of(msg).metrics["lethal_ratio"] == 0.25


def test_a_rolling_costmap_seeing_only_around_the_robot_is_fine() -> None:
    plugin = CostmapPlugin()
    grid = _grid(fill=-1)
    grid.data = [0] * 9 + [-1] * 91  # 91% unknown
    plugin.on_message("/global_costmap/costmap", grid)
    assert "MAP-003" not in plugin.get_status().codes


def test_a_building_surrounded_by_unknown_is_fine() -> None:
    msg = _grid()
    msg.data = [-1] * 85 + [0] * 15
    assert _status_of(msg).codes == {}


def test_a_nearly_blank_grid_warns_and_a_blank_one_errors() -> None:
    msg = _grid(100, 100)
    msg.data = [-1] * 9950 + [0] * 50
    assert _status_of(msg).code_severities == {"MAP-003": Severity.WARN}
    assert _status_of(_grid(fill=-1)).code_severities == {"MAP-003": Severity.ERROR}


def test_a_size_change_is_flagged_for_a_while() -> None:
    plugin = CostmapPlugin()
    plugin.on_tick(100.0)
    plugin.on_message("/costmap", _grid(10, 10))
    plugin.on_message("/costmap", _grid(20, 10))
    status = plugin.get_status()
    assert status.code_severities == {"MAP-004": Severity.WARN}
    assert "10 x 10 at 0.05 m to 20 x 10 at 0.05 m" in status.codes["MAP-004"]
    plugin.on_tick(111.0)
    assert plugin.get_status().codes == {}


def test_a_resolution_change_is_flagged() -> None:
    plugin = CostmapPlugin()
    plugin.on_message("/costmap", _grid(resolution=0.05))
    plugin.on_message("/costmap", _grid(resolution=0.1))
    assert "MAP-004" in plugin.get_status().codes


def test_an_update_changes_the_grid() -> None:
    plugin = CostmapPlugin(related_topics=_RELATED)
    plugin.on_tick(100.0)
    plugin.on_message("/costmap", _grid(2, 2))
    plugin.on_tick(102.0)
    plugin.on_message("/costmap_updates", _Update(x=1, y=1))
    assert plugin.get_status().metrics["lethal_ratio"] == 0.25


def test_an_update_before_the_first_grid_is_ignored() -> None:
    plugin = CostmapPlugin(related_topics=_RELATED)
    plugin.on_message("/costmap_updates", _Update())
    assert plugin.get_status().codes == {}


def test_an_update_outside_the_grid_is_flagged_and_not_applied() -> None:
    plugin = CostmapPlugin(related_topics=_RELATED)
    plugin.on_tick(100.0)
    plugin.on_message("/costmap", _grid(2, 2))
    plugin.on_tick(102.0)
    plugin.on_message("/costmap_updates", _Update(x=5))
    status = plugin.get_status()
    assert status.code_severities == {"MAP-005": Severity.WARN}
    assert status.metrics["lethal_ratio"] == 0.0


def test_silence_is_allowed_only_while_updates_keep_coming() -> None:
    plugin = CostmapPlugin(related_topics=_RELATED)
    plugin.on_tick(100.0)
    plugin.on_message("/costmap", _grid())
    assert not plugin.silence_expected("nav_msgs/msg/OccupancyGrid")
    plugin.on_message("/costmap_updates", _Update())
    assert plugin.silence_expected("nav_msgs/msg/OccupancyGrid")
    plugin.on_tick(111.0)
    assert not plugin.silence_expected("nav_msgs/msg/OccupancyGrid")


def test_a_large_grid_is_only_recounted_once_a_second() -> None:
    plugin = CostmapPlugin()
    plugin.on_tick(100.0)
    plugin.on_message("/costmap", _grid())
    plugin.on_tick(100.5)
    plugin.on_message("/costmap", _grid(fill=100))
    assert plugin.get_status().metrics["lethal_ratio"] == 0.0
    plugin.on_tick(101.0)
    assert plugin.get_status().metrics["lethal_ratio"] == 1.0


def test_a_grid_slow_to_count_is_counted_less_often(monkeypatch) -> None:
    ticks = iter([0.0, 0.5])  # one analysis taking half a second
    monkeypatch.setattr("testudo.plugins.builtin.costmap.time.perf_counter", lambda: next(ticks))
    plugin = CostmapPlugin()
    plugin.on_tick(100.0)
    plugin.on_message("/costmap", _grid())
    plugin.on_message("/costmap", _grid(fill=100))
    plugin.on_tick(104.0)
    assert plugin.get_status().metrics["lethal_ratio"] == 0.0


def test_the_picture_has_y_up() -> None:
    cells = np.zeros((2, 2), dtype=np.int8)
    cells[0, 0] = 100  # the grid's origin: bottom left
    assert analyse(cells).picture.pixels == bytes([1, 1, 4, 1])


def test_a_picture_is_a_snapshot() -> None:
    plugin = CostmapPlugin(related_topics=_RELATED)
    plugin.on_tick(100.0)
    plugin.on_message("/costmap", _grid(2, 2))
    before = plugin.get_status().picture
    plugin.on_tick(102.0)
    plugin.on_message("/costmap_updates", _Update())
    assert plugin.get_status().picture != before
    assert 4 not in before.pixels


def test_a_large_grid_shrinks_and_a_single_wall_cell_survives() -> None:
    cells = np.zeros((4000, 4000), dtype=np.int8)
    cells[1234, 2345] = 100
    picture = analyse(cells).picture
    assert picture.width <= MAX_PICTURE_WIDTH
    assert picture.height <= MAX_PICTURE_HEIGHT
    assert 4 in picture.pixels


def test_a_grid_that_turned_malformed_keeps_its_last_picture() -> None:
    plugin = CostmapPlugin()
    plugin.on_message("/costmap", _grid())
    plugin.on_message("/costmap", _grid(0, 0))
    status = plugin.get_status()
    assert "MAP-001" in status.codes
    assert status.picture is not None
