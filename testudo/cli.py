"""Command-line entrypoints: `testudo watch|check|replay|plugins`.

Each command loads and validates config up front — fail loud at startup,
per the project's config-handling rule — before doing anything else.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time

import rclpy
import rclpy.node
from diagnostic_msgs.msg import DiagnosticArray
from rich.console import Console
from rich.text import Text

from testudo.core.clock import TestudoClock
from testudo.core.config import ConfigError, TestudoConfig, load_config
from testudo.core.publisher import DiagnosticPublisher
from testudo.core.subscription_manager import SubscriptionManager, TopicReport
from testudo.plugins.base import SEVERITY_COLORS, SEVERITY_LABELS, CheckStatus, Severity
from testudo.plugins.registry import discover_all_plugins

_logger = logging.getLogger("testudo")

DEFAULT_CONFIG_PATH = "config/example_config.yaml"


def _dds_implementation() -> str:
    """The RMW/DDS implementation actually in use (e.g. `rmw_cyclonedds_cpp`).

    Queried from rclpy itself rather than reading `$RMW_IMPLEMENTATION`
    directly -- that variable reflects an explicit override, but ROS 2 has
    a compiled-in default RMW when it's unset, which this still reports
    correctly.
    """
    try:
        return rclpy.get_rmw_implementation_identifier()
    except Exception:
        return os.environ.get("RMW_IMPLEMENTATION", "unknown")
DEFAULT_CHECK_DURATION_SECONDS = 3.0


def _add_config_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-c", "--config", default=DEFAULT_CONFIG_PATH, help="path to the Testudo YAML config file")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="testudo", description="Black-box diagnostics for navigation stacks.")
    parser.add_argument("-v", "--verbose", action="store_true", help="enable debug logging")
    subparsers = parser.add_subparsers(dest="command", required=True)

    watch_parser = subparsers.add_parser("watch", help="live interactive TUI")
    _add_config_arg(watch_parser)
    watch_parser.add_argument(
        "--no-hz", action="store_true", help="don't monitor or report topic publish rate (Hz)"
    )

    check_parser = subparsers.add_parser("check", help="one-shot, non-interactive report (CI/pre-flight)")
    _add_config_arg(check_parser)
    check_parser.add_argument(
        "--no-hz", action="store_true", help="don't monitor or report topic publish rate (Hz)"
    )
    check_parser.add_argument(
        "--duration",
        type=float,
        default=DEFAULT_CHECK_DURATION_SECONDS,
        help="seconds to observe the graph before reporting (default: %(default)s)",
    )

    replay_parser = subparsers.add_parser("replay", help="batch report over a bag, or --watch to scrub it live")
    _add_config_arg(replay_parser)
    replay_parser.add_argument("bag", help="path to the bag to replay")
    replay_parser.add_argument("--watch", action="store_true", help="scrub through the bag in the TUI")

    _add_config_arg(subparsers.add_parser("plugins", help="list discovered plugins and the msg types they cover"))

    return parser


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


def _load_config_or_exit(path: str) -> TestudoConfig:
    try:
        return load_config(path)
    except ConfigError as exc:
        _logger.error("%s", exc)
        sys.exit(2)


def cmd_watch(args: argparse.Namespace) -> int:
    config = _load_config_or_exit(args.config)
    plugins = discover_all_plugins(config.plugins_dir)
    _logger.info(
        "loaded config from %s (%d declared topic type(s)); %d plugin(s) discovered",
        args.config,
        len(config.topics),
        len(plugins),
    )

    # Imported lazily: the TUI's dependency (textual) is only needed here.
    from testudo.core.publisher import build_diagnostic_array
    from testudo.tui.app import TestudoApp
    from testudo.tui.data_source import LiveDataSource

    rclpy.init()
    node = rclpy.create_node("testudo_watch")
    stop_spinning = threading.Event()
    spin_thread: threading.Thread | None = None
    try:
        manager = SubscriptionManager(node, config, plugins, monitor_hz=not args.no_hz)
        manager.start()
        # Textual owns the main thread's event loop, so subscription
        # callbacks need their own thread to actually get serviced -- the
        # TUI's poll timer just reads state that thread has already
        # updated. Periodic rediscovery/stale-stack clearing (`manager.
        # maintain()`) rides along on this same thread rather than the
        # TUI's, since both create/destroy rclpy subscriptions and that
        # isn't safe to interleave with a concurrent `spin_once` elsewhere.
        spin_thread = threading.Thread(target=_spin_until_stopped, args=(node, stop_spinning, manager), daemon=True)
        spin_thread.start()

        diagnostics_publisher = node.create_publisher(DiagnosticArray, config.publish.topic, 10)

        def on_snapshot(snapshot) -> None:
            array = build_diagnostic_array(node.get_clock().now().to_msg(), snapshot.reports, snapshot.overall)
            diagnostics_publisher.publish(array)

        app = TestudoApp(
            data_source=LiveDataSource(manager, config.severity_mode),
            ros_distro=os.environ.get("ROS_DISTRO", "unknown"),
            ros_domain_id=os.environ.get("ROS_DOMAIN_ID", "0"),
            dds_implementation=_dds_implementation(),
            poll_rate_hz=config.publish.rate_hz,
            sim_time_active=TestudoClock.from_node(node).is_sim_time_active(),
            on_snapshot=on_snapshot,
        )
        app.run()
    finally:
        stop_spinning.set()
        if spin_thread is not None:
            spin_thread.join(timeout=2.0)
        node.destroy_node()
        rclpy.shutdown()

    return 0


def _spin_until_stopped(node: rclpy.node.Node, stop_event: threading.Event, manager: SubscriptionManager) -> None:
    while not stop_event.is_set():
        rclpy.spin_once(node, timeout_sec=0.1)
        manager.maintain()


def cmd_check(args: argparse.Namespace) -> int:
    config = _load_config_or_exit(args.config)
    plugins = discover_all_plugins(config.plugins_dir)
    _logger.info(
        "loaded config from %s (%d declared topic type(s)); %d plugin(s) discovered",
        args.config,
        len(config.topics),
        len(plugins),
    )

    rclpy.init()
    node = rclpy.create_node("testudo_check")
    try:
        manager = SubscriptionManager(node, config, plugins, monitor_hz=not args.no_hz)
        manager.start()
        # Publishing runs on its own fixed-rate timer for the whole
        # observation window, decoupled from any single check's sampling
        # rate -- this is what makes `check`'s output bag-recordable, not
        # just printed at the end.
        publisher = DiagnosticPublisher(node, manager, config.severity_mode, config.publish.topic, config.publish.rate_hz)
        try:
            _spin_for(node, args.duration)
        finally:
            publisher.destroy()
        manager.tick()
        reports = manager.reports()
        overall = manager.overall_status(config.severity_mode)
    finally:
        node.destroy_node()
        rclpy.shutdown()

    return _print_report(reports, overall)


def _spin_for(node: rclpy.node.Node, duration_seconds: float) -> None:
    """Process callbacks (via `rclpy.spin_once`) until `duration_seconds` of wall time elapses."""
    deadline = time.monotonic() + duration_seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        rclpy.spin_once(node, timeout_sec=remaining)


def _print_report(reports: list[TopicReport], overall: CheckStatus) -> int:
    """Print one line per topic plus the aggregated summary; return the exit code it implies.

    Exit codes follow the common CI convention: 0 OK, 1 WARN, 2 ERROR/STALE.
    Which severity drives `overall` (worst vs. weighted) is decided by
    `SubscriptionManager.overall_status` per `config.severity_mode` --
    printing/exit-coding here just reads the result, so both always agree
    with what got published to `/diagnostics`. Colored via `rich.Text`
    (content is always literal there, never markup, so a status message
    that happens to contain square brackets can't be misread as
    formatting); `Console` auto-disables color when stdout isn't a terminal.
    """
    console = Console()
    if not reports:
        _logger.info("no topics discovered")
        return 0

    for report in sorted(reports, key=lambda r: (-r.status.severity, r.topic)):
        console.print(_topic_line(report))

    console.print()
    console.print(_status_line(overall))

    if overall.severity == Severity.OK:
        return 0
    if overall.severity == Severity.WARN:
        return 1
    return 2


def _topic_line(report: TopicReport) -> Text:
    color = SEVERITY_COLORS.get(report.status.severity, "white")
    label = SEVERITY_LABELS.get(report.status.severity, str(report.status.severity))
    detail = ", ".join(f"{key}={value}" for key, value in report.status.values.items())
    suffix = f" ({detail})" if detail else ""

    line = Text()
    line.append(f"{label:5s} ", style=color)
    line.append(f"{report.topic:30s} ")
    line.append(f"[{report.tier:6s}] ", style="dim")
    line.append(f"{report.msg_type:35s} ")
    line.append(f"{report.status.message}{suffix}")
    return line


def _status_line(status: CheckStatus) -> Text:
    color = SEVERITY_COLORS.get(status.severity, "white")
    label = SEVERITY_LABELS.get(status.severity, str(status.severity))
    detail = ", ".join(f"{key}={value}" for key, value in status.values.items())
    suffix = f" ({detail})" if detail else ""

    line = Text()
    line.append(f"[{label}]", style=color)
    line.append(f" {status.message}{suffix}")
    return line


def cmd_replay(args: argparse.Namespace) -> int:
    config = _load_config_or_exit(args.config)
    plugins = discover_all_plugins(config.plugins_dir)
    _logger.info(
        "loaded config from %s (%d declared topic type(s)); %d plugin(s) discovered",
        args.config,
        len(config.topics),
        len(plugins),
    )

    # Imported lazily so commands that never touch a bag don't require
    # rosbag2_py to be installed.
    from testudo.core.replay import replay_bag

    rclpy.init()
    node = rclpy.create_node("testudo_replay")
    try:
        try:
            reports, overall = replay_bag(args.bag, config, plugins, node)
        except FileNotFoundError as exc:
            _logger.error("%s", exc)
            return 2

        if args.watch:
            from testudo.tui.app import TestudoApp
            from testudo.tui.data_source import StaticDataSource

            # A finished batch replay, browsable in the same TUI as `watch`
            # (drill-down/filter/sort all work) -- not a frame-exact time
            # scrub, which is a reasonable follow-up rather than core here.
            app = TestudoApp(
                data_source=StaticDataSource(reports, overall),
                ros_distro=os.environ.get("ROS_DISTRO", "unknown"),
                ros_domain_id=os.environ.get("ROS_DOMAIN_ID", "0"),
                dds_implementation=_dds_implementation(),
                sim_time_active=True,
            )
            app.run()
            return 0
    finally:
        node.destroy_node()
        rclpy.shutdown()

    return _print_report(reports, overall)


def cmd_plugins(args: argparse.Namespace) -> int:
    config = _load_config_or_exit(args.config)
    discovered = discover_all_plugins(config.plugins_dir)
    if not discovered:
        _logger.info("no plugins discovered")
        return 0
    for plugin in discovered:
        msg_types = ", ".join(plugin.plugin_class.msg_types())
        print(f"{plugin.name:20s} [{plugin.source:8s}] handles: {msg_types}")
    return 0


_COMMANDS = {
    "watch": cmd_watch,
    "check": cmd_check,
    "replay": cmd_replay,
    "plugins": cmd_plugins,
}


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)
    return _COMMANDS[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
