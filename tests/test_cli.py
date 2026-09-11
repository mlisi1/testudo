"""Unit tests for CLI config-path resolution -- no rclpy/live graph needed.

`DEFAULT_CONFIG_PATH` is a module-level constant computed once at import
(from `default_config_path()`, XDG-aware) -- tests that need a specific
default location monkeypatch the constant directly rather than the
environment, since the latter wouldn't be re-read after import anyway.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from testudo import cli
from testudo.core.config import TestudoConfig


def test_load_config_or_exit_explicit_path_that_is_missing_exits(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.yaml"
    with pytest.raises(SystemExit) as exc_info:
        cli._load_config_or_exit(str(missing))
    assert exc_info.value.code == 2


def test_load_config_or_exit_explicit_path_loads_normally(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("severity_mode: weighted\n")

    config, resolved = cli._load_config_or_exit(str(path))

    assert resolved == str(path)
    assert config.severity_mode.value == "weighted"


def test_load_config_or_exit_default_path_missing_yields_empty_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    default_path = tmp_path / "testudo" / "config.yaml"
    monkeypatch.setattr(cli, "DEFAULT_CONFIG_PATH", str(default_path))
    assert not default_path.exists()

    config, resolved = cli._load_config_or_exit(None)

    assert resolved == str(default_path)
    assert config == TestudoConfig()


def test_load_config_or_exit_default_path_present_loads_it(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    default_path = tmp_path / "testudo" / "config.yaml"
    default_path.parent.mkdir(parents=True)
    default_path.write_text("severity_mode: both\n")
    monkeypatch.setattr(cli, "DEFAULT_CONFIG_PATH", str(default_path))

    config, resolved = cli._load_config_or_exit(None)

    assert resolved == str(default_path)
    assert config.severity_mode.value == "both"


def test_default_config_path_is_not_inside_the_repo() -> None:
    """Regression: the old default (`config/example_config.yaml`) pointed at a file tracked
    in the repo -- the Options screen's live persistence meant every exclude rule added
    silently dirtied that file's git status instead of a per-user config."""
    assert "example_config" not in cli.DEFAULT_CONFIG_PATH
    assert Path(cli.DEFAULT_CONFIG_PATH).is_absolute()
    assert Path(cli.DEFAULT_CONFIG_PATH).name == "config.yaml"
    assert Path(cli.DEFAULT_CONFIG_PATH).parent.name == "testudo"
