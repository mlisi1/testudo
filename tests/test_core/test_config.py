"""Unit tests for the config schema and loader/validator.

All synthetic YAML — no live ROS graph or robot needed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from testudo.core.config import (
    ConfigError,
    SeverityMode,
    load_config,
)


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text)
    return path


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "does_not_exist.yaml")


def test_invalid_yaml_raises(tmp_path: Path) -> None:
    path = _write(tmp_path, "topics: [this is not: valid: yaml")
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_config(path)


def test_empty_file_yields_defaults(tmp_path: Path) -> None:
    path = _write(tmp_path, "")
    config = load_config(path)
    assert config.topics == {}
    assert config.actions == []
    assert config.tf == []
    assert config.severity_mode is SeverityMode.WORST
    assert config.publish.rate_hz == 5.0
    assert config.publish.topic == "/diagnostics"


def test_full_valid_config_parses(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        severity_mode: weighted
        publish:
          rate_hz: 10.0
          topic: /custom_diagnostics
        topics:
          nav_msgs/msg/Odometry:
            - name: /odom
              weight: 2
              thresholds:
                rate_hz:
                  green: 20.0
                  orange: 10.0
                  red: 5.0
        actions:
          - name: navigate_to_pose
            action_type: nav2_msgs/action/NavigateToPose
            weight: 3
        tf:
          - parent: map
            child: odom
        """,
    )
    config = load_config(path)

    assert config.severity_mode is SeverityMode.WEIGHTED
    assert config.publish.rate_hz == 10.0
    assert config.publish.topic == "/custom_diagnostics"

    odom_entries = config.topics["nav_msgs/msg/Odometry"]
    assert len(odom_entries) == 1
    odom = odom_entries[0]
    assert odom.name == "/odom"
    assert odom.weight == 2.0
    assert odom.thresholds["rate_hz"].green == 20.0
    assert odom.thresholds["rate_hz"].orange == 10.0
    assert odom.thresholds["rate_hz"].red == 5.0

    assert len(config.actions) == 1
    action = config.actions[0]
    assert action.name == "navigate_to_pose"
    assert action.action_type == "nav2_msgs/action/NavigateToPose"
    assert action.weight == 3.0

    tf_pair = config.tf[0]
    assert tf_pair.parent == "map"
    assert tf_pair.child == "odom"


def test_unknown_top_level_key_raises(tmp_path: Path) -> None:
    path = _write(tmp_path, "bogus_key: true")
    with pytest.raises(ConfigError, match="unknown top-level key"):
        load_config(path)


def test_top_level_must_be_mapping(tmp_path: Path) -> None:
    path = _write(tmp_path, "- just\n- a\n- list\n")
    with pytest.raises(ConfigError, match="top level must be a mapping"):
        load_config(path)


def test_topics_must_be_mapping(tmp_path: Path) -> None:
    path = _write(tmp_path, "topics: [not, a, mapping]")
    with pytest.raises(ConfigError, match="'topics' must be a mapping"):
        load_config(path)


def test_topic_entry_requires_name(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        topics:
          nav_msgs/msg/Odometry:
            - weight: 1
        """,
    )
    with pytest.raises(ConfigError, match="missing required string 'name'"):
        load_config(path)


def test_topic_weight_must_be_numeric(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        topics:
          nav_msgs/msg/Odometry:
            - name: /odom
              weight: "heavy"
        """,
    )
    with pytest.raises(ConfigError, match="weight must be numeric"):
        load_config(path)


def test_threshold_unknown_key_raises(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        topics:
          nav_msgs/msg/Odometry:
            - name: /odom
              thresholds:
                rate_hz:
                  green: 1.0
                  purple: 2.0
        """,
    )
    with pytest.raises(ConfigError, match="unknown key"):
        load_config(path)


def test_action_requires_action_type(tmp_path: Path) -> None:
    path = _write(tmp_path, "actions:\n  - name: navigate_to_pose\n")
    with pytest.raises(ConfigError, match="missing required string 'action_type'"):
        load_config(path)


def test_tf_requires_parent_and_child(tmp_path: Path) -> None:
    path = _write(tmp_path, "tf:\n  - parent: map\n")
    with pytest.raises(ConfigError, match="missing required string 'child'"):
        load_config(path)


def test_action_thresholds_parse(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        actions:
          - name: navigate_to_pose
            action_type: nav2_msgs/action/NavigateToPose
            thresholds:
              success_rate:
                green: 0.9
                orange: 0.7
        """,
    )
    config = load_config(path)
    action = config.actions[0]
    assert action.thresholds["success_rate"].green == 0.9
    assert action.thresholds["success_rate"].orange == 0.7


def test_action_thresholds_default_to_empty(tmp_path: Path) -> None:
    path = _write(tmp_path, "actions:\n  - name: spin\n    action_type: nav2_msgs/action/Spin\n")
    config = load_config(path)
    assert config.actions[0].thresholds == {}


def test_action_entry_rejects_unknown_key(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "actions:\n  - name: spin\n    action_type: nav2_msgs/action/Spin\n    bogus: true\n",
    )
    with pytest.raises(ConfigError, match="unknown key"):
        load_config(path)


def test_invalid_severity_mode_raises(tmp_path: Path) -> None:
    path = _write(tmp_path, "severity_mode: extra_worst")
    with pytest.raises(ConfigError, match="severity_mode"):
        load_config(path)


def test_publish_rate_must_be_positive(tmp_path: Path) -> None:
    path = _write(tmp_path, "publish:\n  rate_hz: -1.0\n")
    with pytest.raises(ConfigError, match="rate_hz must be a positive number"):
        load_config(path)


def test_shipped_example_config_is_valid() -> None:
    example = Path(__file__).resolve().parents[2] / "config" / "example_config.yaml"
    config = load_config(example)
    assert "nav_msgs/msg/Odometry" in config.topics
    assert config.actions
    assert config.tf
    odom = config.topics["nav_msgs/msg/Odometry"][0]
    assert odom.related_topics == {"cmd_vel": "/cmd_vel"}


def test_related_topics_parses_logical_name_to_topic_mapping(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        topics:
          nav_msgs/msg/Odometry:
            - name: /odom
              related_topics:
                cmd_vel: /cmd_vel
        """,
    )
    config = load_config(path)
    odom = config.topics["nav_msgs/msg/Odometry"][0]
    assert odom.related_topics == {"cmd_vel": "/cmd_vel"}


def test_related_topics_defaults_to_empty(tmp_path: Path) -> None:
    path = _write(tmp_path, "topics:\n  nav_msgs/msg/Odometry:\n    - name: /odom\n")
    config = load_config(path)
    assert config.topics["nav_msgs/msg/Odometry"][0].related_topics == {}


def test_related_topics_must_be_a_mapping(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "topics:\n  nav_msgs/msg/Odometry:\n    - name: /odom\n      related_topics: [not, a, mapping]\n",
    )
    with pytest.raises(ConfigError, match="related_topics must be a mapping"):
        load_config(path)


def test_related_topics_value_must_be_a_non_empty_string(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "topics:\n  nav_msgs/msg/Odometry:\n    - name: /odom\n      related_topics:\n        cmd_vel: 5\n",
    )
    with pytest.raises(ConfigError, match="must be a non-empty string"):
        load_config(path)


def test_topic_entry_rejects_unknown_key(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "topics:\n  nav_msgs/msg/Odometry:\n    - name: /odom\n      bogus: true\n",
    )
    with pytest.raises(ConfigError, match="unknown key"):
        load_config(path)
