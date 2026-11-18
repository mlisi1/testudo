"""Unit tests for editing config text in place: everything not edited must stay byte for byte."""
from __future__ import annotations

import difflib
from pathlib import Path

import pytest
import yaml

from testudo.core.config import ConfigError
from testudo.core.config_editor import remove_value, set_value, sync_list

_CONFIG = """\
# head comment
severity_mode: worst   # why worst

topics:
  sensor_msgs/msg/Imu:
    - name: /imu     # the main one
      weight: 1.50
      related_topics: {odom: /odom}
      thresholds:
        stuck_count: {warn: 1e-4, error: null}
        gyro_bias_radps:
          warn: 0.10   # measured on the bench
          error: ~
    # the spare
    - name: "/imu2"

exclude_topics:
  - /a   # why a
  # about b
  - pattern: "_b$"
    type: regex

# about faults
faults:
  retention: session
"""


def _changes(before: str, after: str) -> list[str]:
    """Only the changed lines, prefixed + or -."""
    diff = difflib.ndiff(before.splitlines(), after.splitlines())
    return [line for line in diff if line[0] in "+-"]


def test_a_scalar_changes_in_place_and_keeps_its_comment() -> None:
    edited = set_value(_CONFIG, ("topics", "sensor_msgs/msg/Imu", 0, "thresholds", "gyro_bias_radps", "warn"), 0.25)
    assert _changes(_CONFIG, edited) == [
        "-           warn: 0.10   # measured on the bench",
        "+           warn: 0.25   # measured on the bench",
    ]


def test_a_new_key_lands_at_the_end_of_its_mapping_at_its_indentation() -> None:
    edited = set_value(_CONFIG, ("faults", "recent_window_seconds"), 60)
    assert _changes(_CONFIG, edited) == ["+   recent_window_seconds: 60"]
    assert edited.endswith("  retention: session\n  recent_window_seconds: 60\n")


def test_a_new_nested_key_is_created_inside_a_list_item() -> None:
    edited = set_value(_CONFIG, ("topics", "sensor_msgs/msg/Imu", 1, "codes", "IMU-005"), "ok")
    assert _changes(_CONFIG, edited) == ["+       codes:", "+         IMU-005: ok"]
    assert yaml.safe_load(edited)["topics"]["sensor_msgs/msg/Imu"][1] == {"name": "/imu2", "codes": {"IMU-005": "ok"}}


def test_a_new_top_level_key_goes_last() -> None:
    edited = set_value(_CONFIG, ("plugins_dir",), "/opt/checks")
    assert _changes(_CONFIG, edited) == ["+ ", "+ plugins_dir: /opt/checks"]


def test_a_value_inside_a_flow_mapping_rewrites_only_that_entry() -> None:
    path = ("topics", "sensor_msgs/msg/Imu", 0, "related_topics", "cmd_vel")
    edited = set_value(_CONFIG, path, "/cmd_vel")
    assert yaml.safe_load(edited)["topics"]["sensor_msgs/msg/Imu"][0]["related_topics"] == {"odom": "/odom", "cmd_vel": "/cmd_vel"}
    assert _changes(_CONFIG, edited) == [
        "-       related_topics: {odom: /odom}",
        "+       related_topics:",
        "+         odom: /odom",
        "+         cmd_vel: /cmd_vel",
    ]


def test_a_key_with_no_value_gets_its_children() -> None:
    assert yaml.safe_load(set_value("codes:\nweight: 1\n", ("codes", "X"), "ok")) == {"codes": {"X": "ok"}, "weight": 1}


def test_an_empty_file_and_a_comment_only_file_get_the_value() -> None:
    assert set_value("", ("a", "b"), 1) == "a:\n  b: 1\n"
    assert set_value("# only a comment\n", ("a",), 1) == "# only a comment\n\na: 1\n"


def test_a_flow_style_document_is_rewritten() -> None:
    assert set_value("{}\n", ("a",), 1) == "a: 1\n"


def test_a_file_without_a_final_newline_stays_valid() -> None:
    assert yaml.safe_load(set_value("a:\n  b: 1", ("a", "c"), 2)) == {"a": {"b": 1, "c": 2}}


def test_removing_a_block_keeps_the_comment_above_its_neighbour() -> None:
    edited = remove_value(_CONFIG, ("exclude_topics",))
    assert "# about faults\nfaults:" in edited
    assert all(line.startswith("-") or line == "- " for line in _changes(_CONFIG, edited))


def test_removing_the_last_key_of_a_mapping_removes_the_mapping() -> None:
    edited = remove_value(_CONFIG, ("faults", "retention"))
    assert "faults" not in yaml.safe_load(edited)


def test_removing_the_first_key_of_a_list_item_keeps_the_item() -> None:
    edited = remove_value("items:\n  - name: a  # first\n    # the weight\n    weight: 1\n  - name: b\n", ("items", 0, "name"))
    assert edited == "items:\n    # the weight\n  - weight: 1\n  - name: b\n"


def test_replacing_the_first_key_of_a_list_item_with_a_block_keeps_the_item() -> None:
    edited = set_value("items:\n  - name: a\n    weight: 1\n", ("items", 0, "name"), {"first": 1})
    assert yaml.safe_load(edited) == {"items": [{"name": {"first": 1}, "weight": 1}]}


def test_removing_what_is_not_there_changes_nothing() -> None:
    assert remove_value(_CONFIG, ("faults", "nothing")) == _CONFIG


def test_removing_a_key_from_a_flow_mapping() -> None:
    assert yaml.safe_load(remove_value("a: {b: 1, c: 2}\n", ("a", "b"))) == {"a": {"c": 2}}


def test_a_list_keeps_the_lines_of_the_items_that_stay() -> None:
    edited = sync_list(_CONFIG, ("exclude_topics",), ["/a", "/c"])
    assert "  - /a   # why a\n" in edited
    assert "  - /c\n" in edited
    assert "_b$" not in edited
    assert "# about faults\nfaults:" in edited


def test_a_list_item_is_added_at_the_list_s_own_indentation() -> None:
    edited = sync_list(_CONFIG, ("exclude_topics",), ["/a", {"pattern": "_b$", "type": "regex"}, {"pattern": "x$", "type": "regex"}])
    assert _changes(_CONFIG, edited) == ["+   - pattern: x$", "+     type: regex"]


def test_an_unchanged_list_changes_nothing() -> None:
    assert sync_list(_CONFIG, ("exclude_topics",), ["/a", {"pattern": "_b$", "type": "regex"}]) == _CONFIG


def test_a_flow_list_becomes_a_block_list() -> None:
    assert yaml.safe_load(sync_list("exclude_topics: []\na: 1\n", ("exclude_topics",), ["/a"])) == {"exclude_topics": ["/a"], "a": 1}


def test_invalid_yaml_is_refused() -> None:
    with pytest.raises(ConfigError, match="isn't valid YAML"):
        set_value("a: [1, 2\n", ("a",), 1)


def test_the_example_config_only_changes_where_it_is_edited() -> None:
    text = (Path(__file__).parents[2] / "config" / "example_config.yaml").read_text()
    edited = set_value(text, ("topics", "sensor_msgs/msg/Imu", 0, "codes", "IMU-005"), "ok")
    assert _changes(text, edited) == ["+       codes:", "+         IMU-005: ok"]
