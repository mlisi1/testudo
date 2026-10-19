"""TF tree view: synthetic child maps, no messages needed."""
from __future__ import annotations

from rich.text import Text

from testudo.plugins.builtin.tf_tree_layout import tree_lines


def _plain(lines: list[str]) -> list[str]:
    return [Text.from_markup(line).plain for line in lines]


def test_single_chain_is_one_line() -> None:
    assert _plain(tree_lines("map", {"map": ["odom"], "odom": ["base_link"]})) == ["map ─ odom ─ base_link"]


def test_split_branches_with_runs_and_folded_subtrees() -> None:
    children = {
        "map": ["odom"],
        "odom": ["base_link"],
        "base_link": ["velodyne", "chassis", "camera"],
        "velodyne": ["velodyne_holder"],
        "camera": ["color", "depth"],
        "color": ["color_optical"],
    }
    assert _plain(tree_lines("map", children)) == [
        "map ─ odom ─ base_link ┬ camera (3)",
        "                       ├ chassis",
        "                       └ velodyne ─ velodyne_holder",
    ]


def test_branches_past_the_limit_fold_into_a_count() -> None:
    children = {"base": [f"f{i}" for i in range(6)], "f5": ["f5_child"]}
    lines = _plain(tree_lines("base", children))
    assert lines[0] == "base ┬ f0"
    assert lines[-1] == "     └ … (3 more)"
    assert len(lines) == 5


def test_multi_parent_frame_is_drawn_once() -> None:
    children = {"a": ["b", "c"], "b": ["d"], "c": ["d"]}
    lines = _plain(tree_lines("a", children))
    assert sum(line.count("d") for line in lines) == 1
