"""launch/testudo.launch.py: the node it builds, without launching anything."""
from __future__ import annotations

import importlib.util
from pathlib import Path

from launch import LaunchContext
from launch.actions import DeclareLaunchArgument, OpaqueFunction

LAUNCH_FILE = Path(__file__).resolve().parent.parent / "launch" / "testudo.launch.py"


def _node_for(**launch_args: str):
    spec = importlib.util.spec_from_file_location("testudo_launch", LAUNCH_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    description = module.generate_launch_description()
    context = LaunchContext()
    for entity in description.entities:
        if isinstance(entity, DeclareLaunchArgument):
            context.launch_configurations[entity.name] = launch_args.get(entity.name, entity.default_value[0].text)
    opaque = next(entity for entity in description.entities if isinstance(entity, OpaqueFunction))
    (node,) = opaque.execute(context)
    return node


def _command(node) -> str:
    return " ".join(part.text if hasattr(part, "text") else str(part) for argument in node.cmd for part in argument)


def test_launch_runs_watch_headless_with_the_given_config() -> None:
    node = _node_for(config="/robot/testudo.yaml")
    assert node.node_package == "testudo" and node.node_executable == "testudo"
    command = _command(node)
    assert "watch --headless -c /robot/testudo.yaml" in command


def test_launch_without_config_uses_the_default_location() -> None:
    node = _node_for()
    command = _command(node)
    assert "watch --headless" in command and " -c " not in command
