"""Pure string-in/string-out tests -- no live process needed."""
from __future__ import annotations

from testudo.core.node_profiling.cmdline_parse import parse_remapped_node_name


def test_no_remap_returns_none() -> None:
    assert parse_remapped_node_name(["controller_server", "--ros-args"]) is None


def test_two_token_remap() -> None:
    cmdline = ["controller_server", "--ros-args", "-r", "__node:=controller_server"]
    assert parse_remapped_node_name(cmdline) == "/controller_server"


def test_two_token_remap_with_namespace() -> None:
    cmdline = ["controller_server", "--ros-args", "-r", "__node:=controller_server", "-r", "__ns:=/robot1"]
    assert parse_remapped_node_name(cmdline) == "/robot1/controller_server"


def test_concatenated_flag_and_value() -> None:
    cmdline = ["controller_server", "-r__node:=controller_server"]
    assert parse_remapped_node_name(cmdline) == "/controller_server"


def test_remap_equals_flag() -> None:
    cmdline = ["controller_server", "--remap=__node:=controller_server"]
    assert parse_remapped_node_name(cmdline) == "/controller_server"


def test_namespace_without_node_name_returns_none() -> None:
    cmdline = ["controller_server", "-r", "__ns:=/robot1"]
    assert parse_remapped_node_name(cmdline) is None


def test_empty_cmdline_returns_none() -> None:
    assert parse_remapped_node_name([]) is None


def test_unrelated_flags_ignored() -> None:
    cmdline = ["amcl", "--ros-args", "-p", "use_sim_time:=true", "-r", "__node:=amcl"]
    assert parse_remapped_node_name(cmdline) == "/amcl"
