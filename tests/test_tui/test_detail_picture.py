"""Half-block pictures: synthetic pixels in, markup out -- no Textual app needed."""
from __future__ import annotations

import pytest
from rich.text import Text

from testudo.core.topic_report import TopicReport
from testudo.plugins.base import CheckStatus, Severity
from testudo.plugins.picture import StatusPicture
from testudo.tui.detail_picture import picture_lines
from testudo.tui.detail_render import render_detail

_PALETTE = ("#000000", "#444444", "#ff0000")


def _picture(width: int, height: int, marked: tuple[int, int] | None = None) -> StatusPicture:
    pixels = bytearray([1] * (width * height))
    if marked is not None:
        pixels[marked[1] * width + marked[0]] = 2
    return StatusPicture(width, height, bytes(pixels), _PALETTE)


def test_a_picture_must_have_every_pixel() -> None:
    with pytest.raises(ValueError, match="needs 4 pixels"):
        StatusPicture(2, 2, b"\x00", _PALETTE)


def test_two_pixel_rows_share_one_line() -> None:
    picture = StatusPicture(2, 2, bytes([0, 1, 2, 2]), _PALETTE)
    assert picture_lines(picture, 2, 1) == ["[#000000 on #ff0000]▀[/][#444444 on #ff0000]▀[/]"]


def test_equal_neighbours_are_merged_into_one_run() -> None:
    assert picture_lines(_picture(4, 2), 4, 1) == ["[#444444 on #444444]▀▀▀▀[/]"]


def test_a_picture_keeps_its_shape_inside_the_pane() -> None:
    lines = picture_lines(_picture(100, 100), 80, 10)
    assert len(lines) == 10
    assert {Text.from_markup(line).cell_len for line in lines} == {20}


def test_a_small_picture_is_enlarged_to_the_pane() -> None:
    lines = picture_lines(_picture(4, 4), 40, 8)
    assert len(lines) == 8
    assert Text.from_markup(lines[0]).cell_len == 16


def test_one_marked_pixel_survives_shrinking() -> None:
    lines = picture_lines(_picture(200, 200, marked=(137, 61)), 20, 5)
    assert "#ff0000" in "".join(lines)


def test_nothing_is_drawn_without_room() -> None:
    assert picture_lines(_picture(4, 4), 0, 5) == []
    assert picture_lines(_picture(4, 4), 5, 0) == []


def _report(**status_kwargs) -> TopicReport:
    status = CheckStatus(Severity.OK, "l", "m", {"size": "4 x 4"}, picture=_picture(40, 40), **status_kwargs)
    return TopicReport("/map", "nav_msgs/msg/OccupancyGrid", "full", status)


def test_the_detail_panel_draws_the_picture_in_the_rows_left() -> None:
    rows = render_detail(_report(), width=80, height=12).split("\n")
    assert len(rows) == 12
    assert sum("▀" in row for row in rows) == 10


def test_the_codes_stay_visible_under_the_picture() -> None:
    plain = Text.from_markup(render_detail(_report(codes={"MAP-002": "lethal"}), width=80, height=12)).plain.split("\n")
    assert len(plain) == 12
    assert plain[-1] == "[MAP-002] lethal"


def test_a_short_pane_leaves_the_picture_out() -> None:
    assert "▀" not in render_detail(_report(), width=80, height=4)


def test_an_unknown_pane_height_leaves_the_picture_out() -> None:
    assert "▀" not in render_detail(_report(), width=80)
