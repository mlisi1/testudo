"""Unit tests for plugin discovery: both the packaged and light-path routes."""
from __future__ import annotations

import shutil
from pathlib import Path
from unittest import mock

from testudo.plugins import registry
from testudo.plugins.base import CheckPlugin
from testudo.plugins.builtin.dummy import DummyPlugin
from testudo.plugins.registry import (
    discover_all_plugins,
    discover_light_path_plugins,
    discover_packaged_plugins,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def test_packaged_dummy_plugin_is_discovered() -> None:
    discovered = discover_packaged_plugins()
    names = {plugin.name: plugin for plugin in discovered}
    assert "dummy" in names
    assert names["dummy"].plugin_class is DummyPlugin
    assert names["dummy"].source == "packaged"


def test_light_path_plugin_is_discovered_from_directory() -> None:
    discovered = discover_light_path_plugins(FIXTURES_DIR)
    names = [plugin.name for plugin in discovered]
    assert "ExampleLightPlugin" in names
    example = next(p for p in discovered if p.name == "ExampleLightPlugin")
    assert example.source == "light"
    assert issubclass(example.plugin_class, CheckPlugin)
    assert example.plugin_class.msg_types() == ("std_msgs/msg/Bool",)


def test_light_path_discovery_on_missing_directory_returns_empty(tmp_path: Path) -> None:
    assert discover_light_path_plugins(tmp_path / "does_not_exist") == []


def test_light_path_discovery_warns_about_a_missing_directory(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist"
    with mock.patch.object(registry._logger, "warning") as warning:
        discover_light_path_plugins(missing)
    warning.assert_called_once()
    assert missing in warning.call_args.args


def test_light_path_discovery_expands_the_home_directory(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "checks").mkdir()
    shutil.copy(FIXTURES_DIR / "light_plugin_example.py", tmp_path / "checks" / "light_plugin_example.py")
    monkeypatch.setenv("HOME", str(tmp_path))
    names = [plugin.name for plugin in discover_light_path_plugins("~/checks")]
    assert "ExampleLightPlugin" in names


def test_discover_all_plugins_merges_both_paths() -> None:
    discovered = discover_all_plugins(FIXTURES_DIR)
    sources = {plugin.name: plugin.source for plugin in discovered}
    assert sources.get("dummy") == "packaged"
    assert sources.get("ExampleLightPlugin") == "light"


def test_discover_all_plugins_without_plugins_dir_only_finds_packaged() -> None:
    discovered = discover_all_plugins(None)
    assert all(plugin.source == "packaged" for plugin in discovered)
    assert any(plugin.name == "dummy" for plugin in discovered)
