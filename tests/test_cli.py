"""Unit tests for CLI config-path resolution -- no rclpy/live graph needed.

`DEFAULT_CONFIG_PATH` is a module-level constant computed once at import
(from `default_config_path()`, XDG-aware) -- tests that need a specific
default location monkeypatch the constant directly rather than the
environment, since the latter wouldn't be re-read after import anyway.
"""
from __future__ import annotations

import signal
import threading
from pathlib import Path

import pytest

from testudo import cli
from testudo.core.config import TestudoConfig


def test_a_second_stop_signal_cannot_interrupt_shutdown() -> None:
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        cli._ignore_stop_signals()
        assert all(signal.getsignal(sig) == signal.SIG_IGN for sig in previous)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


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


def test_ros_args_go_to_rclpy_not_argparse() -> None:
    argv = ["watch", "--no-hz", "--ros-args", "-p", "use_sim_time:=true"]
    cli_argv, rclpy_args = cli.split_ros_args(argv)
    assert cli_argv == ["watch", "--no-hz"]
    assert rclpy_args == ["testudo", *argv]
    args = cli._build_parser().parse_args(cli_argv)
    assert args.command == "watch" and args.no_hz


def test_use_sim_time_from_ros_args_reaches_the_node() -> None:
    import rclpy

    from testudo.core.clock import TestudoClock

    _cli_argv, rclpy_args = cli.split_ros_args(["watch", "--ros-args", "-p", "use_sim_time:=true"])
    context = rclpy.Context()
    rclpy.init(args=rclpy_args, context=context)
    try:
        node = rclpy.create_node("testudo_test_sim_time", context=context)
        assert TestudoClock.from_node(node).is_sim_time_active()
        node.destroy_node()
    finally:
        rclpy.shutdown(context=context)


def test_headless_poll_loop_publishes_each_poll_until_rclpy_shuts_down(monkeypatch: pytest.MonkeyPatch) -> None:
    polls: list[int] = []
    published: list[str] = []

    class _Source:
        def poll(self) -> str:
            polls.append(1)
            return f"snapshot-{len(polls)}"

    # rclpy.ok() is checked before and after each poll: stop after the third.
    oks = iter([True, True, True, True, True, True, False])
    monkeypatch.setattr(cli.rclpy, "ok", lambda: next(oks, False))
    cli._poll_until_stopped(_Source(), published.append, poll_rate_hz=1000.0)

    assert published == ["snapshot-1", "snapshot-2", "snapshot-3"]


def test_watch_accepts_headless() -> None:
    args = cli._build_parser().parse_args(["watch", "--headless"])
    assert args.command == "watch" and args.headless


class _FailingExecutor:
    """An executor whose next spin hits a context that has just been shut down."""

    def spin_once(self, timeout_sec: float) -> None:
        raise cli.RCLError("failed to initialize wait set: the given context is not valid")


def test_spin_loop_ends_quietly_when_shutdown_lands_mid_spin(monkeypatch) -> None:
    states = iter([True, False])
    monkeypatch.setattr(cli.rclpy, "ok", lambda: next(states))
    cli._spin_until_stopped(_FailingExecutor(), threading.Event(), manager=None)


def test_spin_loop_still_raises_an_rcl_error_while_ros_is_running(monkeypatch) -> None:
    monkeypatch.setattr(cli.rclpy, "ok", lambda: True)
    with pytest.raises(cli.RCLError):
        cli._spin_until_stopped(_FailingExecutor(), threading.Event(), manager=None)
