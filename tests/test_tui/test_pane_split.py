"""Tests for the panes/Detail Panel height split -- pure arithmetic, no Textual."""
from __future__ import annotations

from testudo.tui.pane_split import MIN_DETAIL_ROWS, MIN_PANE_ROWS, detail_cap, detail_height, rendered_rows


def test_rendered_rows_counts_wrapped_lines_ignoring_markup() -> None:
    assert rendered_rows("[b]abc[/b]\nde", 10) == 2
    assert rendered_rows("x" * 25, 10) == 3
    assert rendered_rows("a\n\nb", 10) == 3


def test_cap_gives_the_detail_whatever_the_panes_leave() -> None:
    # 34 rows, a 3-row category: the detail may take up to 30.
    assert detail_cap(34, 3) == 34 - MIN_PANE_ROWS


def test_cap_falls_back_to_the_fixed_share_when_both_want_more() -> None:
    # 60 topics on a 34-row screen: the detail still gets ~45%.
    assert detail_cap(34, 61) == 15


def test_cap_never_starves_either_side_on_a_tiny_screen() -> None:
    assert detail_cap(6, 30) >= MIN_DETAIL_ROWS
    assert detail_cap(3, 30) == 3


def test_short_content_gives_the_rest_back_to_the_panes() -> None:
    # A vitals row: 6 content rows + the border, not a fixed 45%.
    assert detail_height(30, 6) == 7
    assert detail_height(30, 1) == MIN_DETAIL_ROWS


def test_long_content_is_capped() -> None:
    assert detail_height(15, 40) == 15


def test_same_topic_grows_at_once_but_does_not_shrink() -> None:
    assert detail_height(30, 9, previous=7) == 10
    assert detail_height(30, 5, previous=10) == 10
    # ...but never past the cap, even if it was taller before a resize.
    assert detail_height(8, 5, previous=10) == 8
