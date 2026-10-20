"""Unit tests for the config schema and loader/validator.

All synthetic YAML — no live ROS graph or robot needed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from testudo.core.config import (
    ConfigError,
    ExcludeMatchType,
    ExcludeRule,
    NodeProfilingConfig,
    SeverityMode,
    default_config_path,
)
from testudo.core.config_loader import load_config


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
    assert config.exclude_topics == []


def test_exclude_topics_plain_strings_default_to_literal(tmp_path: Path) -> None:
    path = _write(tmp_path, "exclude_topics:\n  - /velodyne_packets\n  - /front_camera/image_raw\n")
    config = load_config(path)
    assert config.exclude_topics == [
        ExcludeRule("/velodyne_packets"),
        ExcludeRule("/front_camera/image_raw"),
    ]
    assert all(rule.match_type is ExcludeMatchType.LITERAL for rule in config.exclude_topics)


def test_exclude_topics_regex_entries(tmp_path: Path) -> None:
    path = _write(tmp_path, "exclude_topics:\n  - pattern: '_debug$'\n    type: regex\n  - pattern: '^/tmp_'\n    type: regex\n")
    config = load_config(path)
    assert config.exclude_topics == [
        ExcludeRule("_debug$", ExcludeMatchType.REGEX),
        ExcludeRule("^/tmp_", ExcludeMatchType.REGEX),
    ]


def test_exclude_topics_must_be_a_list(tmp_path: Path) -> None:
    path = _write(tmp_path, "exclude_topics: not_a_list")
    with pytest.raises(ConfigError, match="'exclude_topics' must be a list"):
        load_config(path)


def test_exclude_topics_entries_must_be_a_string_or_mapping(tmp_path: Path) -> None:
    path = _write(tmp_path, "exclude_topics:\n  - 5\n")
    with pytest.raises(ConfigError, match="exclude_topics entries must be a string or a"):
        load_config(path)


def test_exclude_topics_mapping_requires_non_empty_pattern(tmp_path: Path) -> None:
    path = _write(tmp_path, "exclude_topics:\n  - type: regex\n")
    with pytest.raises(ConfigError, match="non-empty string 'pattern'"):
        load_config(path)


def test_exclude_topics_mapping_rejects_unknown_keys(tmp_path: Path) -> None:
    path = _write(tmp_path, "exclude_topics:\n  - pattern: /foo\n    bogus: 1\n")
    with pytest.raises(ConfigError, match="unknown key"):
        load_config(path)


def test_exclude_topics_type_must_be_literal_or_regex(tmp_path: Path) -> None:
    path = _write(tmp_path, "exclude_topics:\n  - pattern: /foo\n    type: glob\n")
    with pytest.raises(ConfigError, match="must be one of \\[literal, regex\\]"):
        load_config(path)


def test_exclude_topics_invalid_regex_fails_loudly(tmp_path: Path) -> None:
    path = _write(tmp_path, "exclude_topics:\n  - pattern: '['\n    type: regex\n")
    with pytest.raises(ConfigError, match="invalid regex"):
        load_config(path)


def test_exclude_topics_literal_pattern_with_regex_metacharacters_fails_loudly(tmp_path: Path) -> None:
    """A plain string like `_packets$` used to save silently as a literal (and match nothing) -- now fails."""
    path = _write(tmp_path, "exclude_topics:\n  - _packets$\n")
    with pytest.raises(ConfigError, match="did you mean type: regex"):
        load_config(path)


def test_exclude_topics_literal_pattern_allows_topic_name_characters(tmp_path: Path) -> None:
    path = _write(tmp_path, "exclude_topics:\n  - /a_topic/with_underscores123\n  - ~private_topic\n")
    config = load_config(path)
    assert [rule.pattern for rule in config.exclude_topics] == ["/a_topic/with_underscores123", "~private_topic"]


def test_exclude_rule_literal_matches_exact_name_only() -> None:
    rule = ExcludeRule("/velodyne_packets")
    assert rule.matches("/velodyne_packets")
    assert not rule.matches("/velodyne_packets/raw")


def test_exclude_rule_regex_matches_anywhere_unless_anchored() -> None:
    starts_with = ExcludeRule("^/debug_", ExcludeMatchType.REGEX)
    assert starts_with.matches("/debug_camera")
    assert not starts_with.matches("/camera/debug_raw")

    ends_with = ExcludeRule("_debug$", ExcludeMatchType.REGEX)
    assert ends_with.matches("/camera/image_debug")
    assert not ends_with.matches("/debug_camera")


def test_default_config_path_uses_xdg_config_home_when_set(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert default_config_path() == tmp_path / "testudo" / "config.yaml"


def test_default_config_path_falls_back_to_dot_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert default_config_path() == Path.home() / ".config" / "testudo" / "config.yaml"


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


def test_action_related_topics_parse(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        actions:
          - name: navigate_to_pose
            action_type: nav2_msgs/action/NavigateToPose
            related_topics:
              feedback: /navigate_to_pose/_action/feedback
        """,
    )
    config = load_config(path)
    assert config.actions[0].related_topics == {"feedback": "/navigate_to_pose/_action/feedback"}


def test_action_related_topics_default_to_empty(tmp_path: Path) -> None:
    path = _write(tmp_path, "actions:\n  - name: spin\n    action_type: nav2_msgs/action/Spin\n")
    assert load_config(path).actions[0].related_topics == {}


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


def test_node_profiling_defaults(tmp_path: Path) -> None:
    path = _write(tmp_path, "")
    config = load_config(path)
    assert config.node_profiling == NodeProfilingConfig()


def test_node_profiling_full(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        node_profiling:
          enabled: false
          poll_interval_seconds: 1.5
          rediscovery_interval_seconds: 10
          gpu_enabled: false
          publish_topic: /diagnostics/my_nodes
          publish_rate_hz: 2.0
          cpu_percent:
            green: 50
            orange: 90
          memory_mb:
            green: 200
            orange: 800
          memory_slope_mb_per_min:
            green: 5
            orange: 20
          exclude_nodes:
            - /noisy_node
            - pattern: "_debug$"
              type: regex
        """,
    )
    config = load_config(path)
    np = config.node_profiling
    assert np.enabled is False
    assert np.poll_interval_seconds == 1.5
    assert np.rediscovery_interval_seconds == 10.0
    assert np.gpu_enabled is False
    assert np.publish_topic == "/diagnostics/my_nodes"
    assert np.publish_rate_hz == 2.0
    assert np.cpu_percent.green == 50.0
    assert np.cpu_percent.orange == 90.0
    assert np.memory_mb.green == 200.0
    assert np.memory_mb.orange == 800.0
    assert np.memory_slope_mb_per_min.green == 5.0
    assert np.memory_slope_mb_per_min.orange == 20.0
    assert np.exclude_nodes == [
        ExcludeRule("/noisy_node"),
        ExcludeRule("_debug$", ExcludeMatchType.REGEX),
    ]


def test_node_profiling_must_be_a_mapping(tmp_path: Path) -> None:
    path = _write(tmp_path, "node_profiling: not_a_mapping")
    with pytest.raises(ConfigError, match="'node_profiling' must be a mapping"):
        load_config(path)


def test_node_profiling_rejects_unknown_key(tmp_path: Path) -> None:
    path = _write(tmp_path, "node_profiling:\n  bogus: 1\n")
    with pytest.raises(ConfigError, match="node_profiling has unknown key"):
        load_config(path)


def test_node_profiling_enabled_must_be_boolean(tmp_path: Path) -> None:
    path = _write(tmp_path, "node_profiling:\n  enabled: yes_please\n")
    with pytest.raises(ConfigError, match="node_profiling.enabled must be a boolean"):
        load_config(path)


def test_node_profiling_rejects_non_positive_poll_interval(tmp_path: Path) -> None:
    path = _write(tmp_path, "node_profiling:\n  poll_interval_seconds: 0\n")
    with pytest.raises(ConfigError, match="poll_interval_seconds must be a positive number"):
        load_config(path)


def test_node_profiling_rejects_empty_publish_topic(tmp_path: Path) -> None:
    path = _write(tmp_path, "node_profiling:\n  publish_topic: ''\n")
    with pytest.raises(ConfigError, match="publish_topic must be a non-empty string"):
        load_config(path)


def test_exclude_nodes_error_mentions_its_own_key_not_exclude_topics(tmp_path: Path) -> None:
    """Regression: _parse_exclude_rule used to hardcode 'exclude_topics' into every error."""
    path = _write(tmp_path, "node_profiling:\n  exclude_nodes:\n    - pattern: '['\n      type: regex\n")
    with pytest.raises(ConfigError, match="exclude_nodes: invalid regex"):
        load_config(path)


def test_exclude_topics_error_still_mentions_exclude_topics(tmp_path: Path) -> None:
    path = _write(tmp_path, "exclude_topics:\n  - pattern: '['\n    type: regex\n")
    with pytest.raises(ConfigError, match="exclude_topics: invalid regex"):
        load_config(path)


def test_event_driven_defaults_to_false_and_parses(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        topics:
          geometry_msgs/msg/Twist:
            - name: /cmd_vel_nav
              event_driven: true
            - name: /cmd_vel
        """,
    )
    nav, plain = load_config(path).topics["geometry_msgs/msg/Twist"]
    assert nav.event_driven is True
    assert plain.event_driven is False


def test_event_driven_must_be_a_bool(tmp_path: Path) -> None:
    path = _write(tmp_path, "topics:\n  geometry_msgs/msg/Twist:\n    - name: /cmd_vel\n      event_driven: yes please\n")
    with pytest.raises(ConfigError, match="event_driven"):
        load_config(path)


def test_publish_defaults_to_per_plugin_topics_under_testudo(tmp_path: Path) -> None:
    config = load_config(_write(tmp_path, "publish:\n  rate_hz: 5.0\n"))
    assert config.publish.per_plugin is True
    assert config.publish.namespace == "/testudo"


def test_publish_per_plugin_and_namespace_parse_and_validate(tmp_path: Path) -> None:
    config = load_config(_write(tmp_path, "publish:\n  per_plugin: false\n  namespace: /robot/testudo/\n"))
    assert config.publish.per_plugin is False
    assert config.publish.namespace == "/robot/testudo"
    with pytest.raises(ConfigError, match="per_plugin"):
        load_config(_write(tmp_path, "publish:\n  per_plugin: yes please\n"))
    with pytest.raises(ConfigError, match="namespace"):
        load_config(_write(tmp_path, "publish:\n  namespace: testudo\n"))
