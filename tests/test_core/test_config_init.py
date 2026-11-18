"""Unit tests for `testudo config init`: what it declares, and that its output loads."""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from testudo import cli
from testudo.core import config_init
from testudo.core.config_init import GraphSummary, read_graph, render_config
from testudo.core.config_loader import load_config
from testudo.core.discovery import TopicInfo
from testudo.plugins.builtin.actions import ActionsPlugin
from testudo.plugins.builtin.dummy import DummyPlugin
from testudo.plugins.builtin.image_stream import ImageStreamPlugin
from testudo.plugins.builtin.imu import ImuPlugin
from testudo.plugins.builtin.localization import LocalizationPlugin
from testudo.plugins.builtin.tf_watch import TFWatchPlugin
from testudo.plugins.registry import DiscoveredPlugin, discover_all_plugins

_IMU = "sensor_msgs/msg/Imu"
_IMAGE = "sensor_msgs/msg/Image"
_ODOM = "nav_msgs/msg/Odometry"

_PLUGINS = [
    DiscoveredPlugin(name, plugin, "packaged")
    for name, plugin in [
        ("dummy", DummyPlugin),
        ("imu", ImuPlugin),
        ("image_stream", ImageStreamPlugin),
        ("localization", LocalizationPlugin),
        ("tf_watch", TFWatchPlugin),
        ("actions", ActionsPlugin),
    ]
]

_GRAPH = GraphSummary(
    topics={
        "/imu": (_IMU,),
        "/imu2": (_IMU,),
        "/odom": (_ODOM,),
        "/camera/image": (_IMAGE,),
        "/tf": ("tf2_msgs/msg/TFMessage",),
        "/battery": ("sensor_msgs/msg/BatteryState",),
        "/heartbeat": ("std_msgs/msg/Empty",),
        "/navigate_to_pose/_action/status": ("action_msgs/msg/GoalStatusArray",),
    },
    actions=("navigate_to_pose",),
)

#: A commented line that is config rather than prose.
_COMMENTED_CONFIG = re.compile(r"^(\s*)# (\s*(?:thresholds:|[a-z_]+: \{.*\}|- name: \S+|[\w/]+:)\s*)$")


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text)
    return path


def test_the_output_loads(tmp_path: Path) -> None:
    config = load_config(_write(tmp_path, render_config(_GRAPH, _PLUGINS).text))
    assert [entry.name for entry in config.topics[_IMU]] == ["/imu", "/imu2"]
    assert config.topics[_ODOM][0].name == "/odom"
    assert config.actions[0].name == "navigate_to_pose"


def test_nothing_is_tuned_until_a_line_is_uncommented(tmp_path: Path) -> None:
    config = load_config(_write(tmp_path, render_config(_GRAPH, _PLUGINS).text))
    assert all(not entry.levels for entries in config.topics.values() for entry in entries)


def test_only_covered_topics_are_declared() -> None:
    data = yaml.safe_load(render_config(_GRAPH, _PLUGINS).text)
    names = {entry["name"] for entries in data["topics"].values() for entry in entries}
    assert names == {"/imu", "/imu2", "/odom", "/tf"}


def test_a_camera_is_written_commented_out() -> None:
    rendered = render_config(_GRAPH, _PLUGINS)
    assert "  #   - name: /camera/image\n" in rendered.text
    assert rendered.heavy_topics == 1 and rendered.topics == 4 and rendered.actions == 1


def test_uncommenting_everything_gives_the_plugin_defaults(tmp_path: Path) -> None:
    text = render_config(_GRAPH, _PLUGINS).text
    uncommented = "".join(
        _COMMENTED_CONFIG.sub(r"\1\2", line.rstrip("\n")) + "\n" for line in text.splitlines(keepends=True)
    )
    config = load_config(_write(tmp_path, uncommented))
    assert config.topics[_IMAGE][0].name == "/camera/image"
    imu = config.topics[_IMU][0].levels
    for metric, default in ImuPlugin.default_levels().items():
        assert imu[metric].over(None).warn == default.warn
        assert imu[metric].over(None).error == default.error


def test_every_installed_plugin_writes_a_config_that_loads_uncommented(tmp_path: Path) -> None:
    plugins = discover_all_plugins(None)
    topics = {
        f"/topic_{index}": (msg_type,)
        for index, msg_type in enumerate(sorted({kind for item in plugins for kind in item.plugin_class.msg_types()}))
    }
    text = render_config(GraphSummary(topics, ("spin",)), plugins).text
    uncommented = "".join(_COMMENTED_CONFIG.sub(r"\1\2", line) + "\n" for line in text.splitlines())
    assert load_config(_write(tmp_path, uncommented)).actions[0].levels


def test_a_very_small_default_stays_a_number() -> None:
    assert yaml.safe_load(config_init._number(1e-05)) == 1e-05


def test_a_graph_with_nothing_covered_still_loads(tmp_path: Path) -> None:
    graph = GraphSummary({"/battery": ("sensor_msgs/msg/BatteryState",)}, ())
    assert load_config(_write(tmp_path, render_config(graph, _PLUGINS).text)).topics == {}


def test_a_graph_with_only_a_camera_still_loads(tmp_path: Path) -> None:
    graph = GraphSummary({"/camera": (_IMAGE,)}, ())
    assert load_config(_write(tmp_path, render_config(graph, _PLUGINS).text)).topics == {}


def test_read_graph_keeps_what_has_a_publisher(monkeypatch: pytest.MonkeyPatch) -> None:
    infos = {
        "/imu": (_IMU, 1),
        "/goal_pose": ("geometry_msgs/msg/PoseStamped", 0),
        "/rosout": ("rcl_interfaces/msg/Log", 1),
        "/parameter_events": ("rcl_interfaces/msg/ParameterEvent", 1),
        "/tf_static": ("tf2_msgs/msg/TFMessage", 1),
        "/testudo/imu": ("diagnostic_msgs/msg/DiagnosticArray", 1),
        "/spin/_action/status": ("action_msgs/msg/GoalStatusArray", 1),
    }
    monkeypatch.setattr(config_init, "list_topics", lambda _node: [TopicInfo(name, (kind,)) for name, (kind, _n) in infos.items()])
    graph = read_graph(SimpleNamespace(count_publishers=lambda name: infos[name][1]))
    assert "/imu" in graph.topics
    assert not {"/goal_pose", "/rosout", "/parameter_events", "/tf_static", "/testudo/imu"} & set(graph.topics)
    assert graph.actions == ("spin",)


def _args(path: Path, *, force: bool = False) -> SimpleNamespace:
    return SimpleNamespace(config=str(path), force=force, rclpy_args=["testudo"])


def _fake_ros(monkeypatch: pytest.MonkeyPatch, graph: GraphSummary) -> None:
    node = SimpleNamespace(destroy_node=lambda: None)
    monkeypatch.setattr(cli.rclpy, "init", lambda args=None: None)
    monkeypatch.setattr(cli.rclpy, "create_node", lambda _name: node)
    monkeypatch.setattr(cli.rclpy, "try_shutdown", lambda: None)
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(cli, "discover_all_plugins", lambda _directory: _PLUGINS)
    monkeypatch.setattr(config_init, "read_graph", lambda _node: graph)


def test_an_existing_file_is_kept_without_force(tmp_path: Path) -> None:
    path = _write(tmp_path, "severity_mode: both\n")
    assert cli.cmd_config_init(_args(path)) == 2
    assert path.read_text() == "severity_mode: both\n"


def test_force_replaces_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_ros(monkeypatch, _GRAPH)
    path = _write(tmp_path, "severity_mode: both\n")
    assert cli.cmd_config_init(_args(path, force=True)) == 0
    assert "- name: /imu\n" in path.read_text()


def test_the_file_and_its_directory_are_created(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_ros(monkeypatch, _GRAPH)
    path = tmp_path / "new" / "config.yaml"
    assert cli.cmd_config_init(_args(path)) == 0
    assert load_config(path).topics[_IMU]


def test_an_empty_graph_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_ros(monkeypatch, GraphSummary({}, ()))
    path = tmp_path / "config.yaml"
    assert cli.cmd_config_init(_args(path)) == 1
    assert not path.exists()


def test_the_command_line_reaches_config_init() -> None:
    args = cli._build_parser().parse_args(["config", "init", "-c", "/tmp/x.yaml", "--force"])
    assert (args.command, args.config_command, args.config, args.force) == ("config", "init", "/tmp/x.yaml", True)
