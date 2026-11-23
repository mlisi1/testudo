"""Command-line entrypoints: `testudo watch|replay|plugins`.

Each command loads and validates config up front — fail loud at startup,
per the project's config-handling rule — before doing anything else.
"""
from __future__ import annotations

import argparse
import contextlib
import logging
import os
import signal
import sys
import threading
import time

import rclpy
import rclpy.node
from rclpy._rclpy_pybind11 import RCLError
from rclpy.executors import Executor, ExternalShutdownException, SingleThreadedExecutor
from rclpy.utilities import remove_ros_args

from testudo.core.clock import TestudoClock
from testudo.core.config_check import warn_unknown_metrics
from testudo.core.config import ConfigError, TestudoConfig, default_config_path
from testudo.core.config_loader import load_config
from testudo.core.node_profiling.profiler import NodeProfiler
from testudo.core.subscription_manager import SubscriptionManager
from testudo.core.text_report import print_report
from testudo.plugins.registry import discover_all_plugins

_logger = logging.getLogger("testudo")

DEFAULT_CONFIG_PATH = str(default_config_path())


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


def _add_config_arg(parser: argparse.ArgumentParser) -> None:
    # default=None (not DEFAULT_CONFIG_PATH itself): `_load_config_or_exit`
    # needs to tell "the user asked for this file" apart from "nothing was
    # passed" -- a missing file is a hard error in the first case, but not
    # in the second (see its docstring).
    parser.add_argument(
        "-c", "--config", default=None, help=f"path to the Testudo YAML config file (default: {DEFAULT_CONFIG_PATH})"
    )


def _add_no_hz_arg(parser: argparse.ArgumentParser, *, dest: str = "no_hz") -> None:
    parser.add_argument(
        "--no-hz", dest=dest, action="store_true", help="don't monitor or report topic publish rate (Hz)"
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="testudo", description="Black-box diagnostics for navigation stacks.")
    parser.add_argument("-v", "--verbose", action="store_true", help="enable debug logging")
    # Also registered here (into a separate `no_hz_global` dest, merged with
    # the per-subcommand `no_hz` in `main()`) so `--no-hz` works placed
    # before the subcommand too (`testudo --no-hz watch`, or the equivalent
    # `ros2 run testudo testudo --no-hz watch`) -- argparse's subparsers
    # only recognize an option registered on *that* subparser once the
    # subcommand token is consumed, so without this it only worked placed
    # after it. A *shared* dest here doesn't work: the subparser's own
    # default-fill silently resets a shared `no_hz` back to False even when
    # the parent already set it True, which is worse than the original bug
    # (wrong value, not an error) -- hence the separate dest and the merge
    # below instead. Harmless no-op for `replay`/`plugins`, which don't
    # read `no_hz`.
    _add_no_hz_arg(parser, dest="no_hz_global")
    subparsers = parser.add_subparsers(dest="command", required=True)

    watch_parser = subparsers.add_parser("watch", help="live interactive TUI (or --headless: publish only)")
    _add_config_arg(watch_parser)
    _add_no_hz_arg(watch_parser)
    watch_parser.add_argument(
        "--headless",
        action="store_true",
        help="no TUI: keep monitoring and publishing diagnostics until Ctrl-C (robots, launch files, CI)",
    )

    replay_parser = subparsers.add_parser("replay", help="batch report over a bag, or --watch to scrub it live")
    _add_config_arg(replay_parser)
    replay_parser.add_argument("bag", help="path to the bag to replay")
    replay_parser.add_argument("--watch", action="store_true", help="scrub through the bag in the TUI")

    _add_config_arg(subparsers.add_parser("plugins", help="list discovered plugins and the msg types they cover"))

    config_parser = subparsers.add_parser("config", help="work with the config file")
    config_commands = config_parser.add_subparsers(dest="config_command", required=True)
    init_parser = config_commands.add_parser("init", help="write a config for the stack running now")
    _add_config_arg(init_parser)
    init_parser.add_argument("--force", action="store_true", help="replace the file if it already exists")

    return parser


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


def _load_config_or_exit(path: str | None) -> tuple[TestudoConfig, str]:
    """Load `path`, or the default XDG location if `path` is None. Returns (config, resolved path).

    A missing file at an *explicit* `-c/--config` path still fails
    loudly, per the usual "malformed config fails at load time" rule --
    the user asked for that specific file. A missing file at the
    *default* location isn't an error: every config section is optional,
    so no file at all just means "nothing declared or excluded" -- exactly what an empty file would mean too, and the
    default shouldn't require a user to create one just to get that.
    """
    explicit = path is not None
    resolved = path if explicit else DEFAULT_CONFIG_PATH
    if not explicit and not os.path.isfile(resolved):
        _logger.info("no config file found at %s -- using the defaults", resolved)
        return TestudoConfig(), resolved
    try:
        return load_config(resolved), resolved
    except ConfigError as exc:
        _logger.error("%s", exc)
        sys.exit(2)


def _dashboard_can_start() -> bool:
    """False, with the reason logged, when the installed Textual is too old for the dashboard."""
    from testudo.tui.requirement import textual_problem

    problem = textual_problem()
    if problem is not None:
        _logger.error("%s", problem)
    return problem is None


def cmd_watch(args: argparse.Namespace) -> int:
    if not args.headless and not _dashboard_can_start():
        return 2
    config, config_path = _load_config_or_exit(args.config)
    plugins = discover_all_plugins(config.plugins_dir)
    warn_unknown_metrics(config, plugins, config_path)
    _logger.info(
        "loaded config from %s (%d declared topic type(s)); %d plugin(s) discovered",
        config_path,
        len(config.topics),
        len(plugins),
    )

    from testudo.core.live_output import LiveOutput
    from testudo.tui.data_source import LiveDataSource

    rclpy.init(args=args.rclpy_args)
    node = rclpy.create_node("testudo_watch")
    stop_spinning = threading.Event()
    spin_thread: threading.Thread | None = None
    node_profiler: NodeProfiler | None = None
    try:
        manager = SubscriptionManager(
            node,
            config,
            plugins,
            monitor_hz=not args.no_hz,
            max_check_rate_hz=config.max_check_rate_hz,
            hysteresis_required_consecutive=config.debounce_samples,
        )
        # With the TUI, everything from here through app.run() returning can
        # log from a background thread (a missing message-interface package
        # is the common case) while Textual owns the terminal --
        # suppressed_terminal_logging buffers that instead of letting it
        # corrupt the live display, and prints what it caught once the TUI
        # is done. Headless, the terminal is the log's to write to.
        with _terminal_logging(args.headless):
            manager.start()
            # The poll loop (Textual's timer, or the headless loop) owns the
            # main thread, so subscription callbacks need their own thread
            # to actually get serviced -- a poll just reads state that thread
            # has already updated. Periodic rediscovery/stale-stack clearing
            # (`manager.maintain()`) rides along on this same thread, since
            # both create/destroy rclpy subscriptions and that isn't safe to
            # interleave with a concurrent `spin_once` elsewhere.
            executor = _make_executor(node)
            spin_thread = threading.Thread(
                target=_spin_until_stopped, args=(executor, stop_spinning, manager), daemon=True
            )
            spin_thread.start()

            if config.node_profiling.enabled:
                node_profiler = NodeProfiler(
                    node, config.node_profiling, hysteresis_required_consecutive=config.debounce_samples
                )
                # NodeProfiler.start() creates its own rediscovery timer on
                # `node`, so its graph calls land on this same spin thread
                # without any further plumbing here -- see profiler.py.
                node_profiler.start()
            data_source = LiveDataSource(
                manager,
                config.severity_mode,
                config_path=config_path,
                node_profiler=node_profiler,
                faults=config.faults,
                node_profiling=config.node_profiling,
            )
            output = LiveOutput(node, config, plugins, node_profiler, data_source.fault_history)

            def on_snapshot(snapshot) -> None:
                output.severity_mode = data_source.severity_mode
                output.publish(snapshot.reports, snapshot.overall, snapshot.time_s)

            if args.headless:
                _poll_until_stopped(data_source, on_snapshot, config.publish.rate_hz)
            else:
                _run_tui(node, data_source, on_snapshot, config.publish.rate_hz)
    finally:
        _ignore_stop_signals()
        stop_spinning.set()
        if spin_thread is not None:
            spin_thread.join(timeout=2.0)
        if node_profiler is not None:
            node_profiler.close()
        node.destroy_node()
        # Ctrl-C/SIGTERM headless: rclpy's own signal handler may already have shut down.
        rclpy.try_shutdown()

    return 0


def _ignore_stop_signals() -> None:
    """Let shutdown finish: a launch file and a terminal can each send their own SIGINT."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)


def _terminal_logging(headless: bool) -> contextlib.AbstractContextManager:
    """Buffer log output while the TUI owns the terminal (`suppressed_terminal_logging`); headless, leave it be."""
    if headless:
        return contextlib.nullcontext()
    from testudo.tui.log_summary import suppressed_terminal_logging

    return suppressed_terminal_logging()


def _run_tui(node: rclpy.node.Node, data_source, on_snapshot, poll_rate_hz: float) -> None:
    """The live dashboard, polling `data_source` (and publishing via `on_snapshot`) at `poll_rate_hz`."""
    # Imported lazily: textual is only needed with the TUI.
    from testudo.tui.app import TestudoApp
    from testudo.tui.display_prefs import default_display_prefs_path, load_display_prefs
    from testudo.tui.terminal_title import TITLE, terminal_title

    prefs_path = default_display_prefs_path()
    app = TestudoApp(
        display_prefs=load_display_prefs(prefs_path),
        display_prefs_path=prefs_path,
        data_source=data_source,
        ros_distro=os.environ.get("ROS_DISTRO", "unknown"),
        ros_domain_id=os.environ.get("ROS_DOMAIN_ID", "0"),
        dds_implementation=_dds_implementation(),
        poll_rate_hz=poll_rate_hz,
        sim_time_active=TestudoClock.from_node(node).is_sim_time_active(),
        on_snapshot=on_snapshot,
    )
    with terminal_title(TITLE, window_icon=True):
        app.run()


def _poll_until_stopped(data_source, on_snapshot, poll_rate_hz: float) -> None:
    """`watch --headless`: poll and publish at `poll_rate_hz` until Ctrl-C/SIGTERM.

    The same poll the TUI's timer runs -- ticking the manager, sampling
    node profiling, publishing every diagnostics topic -- minus the
    drawing, for a robot with no terminal attached, a launch file, or CI.
    rclpy's signal handler shuts the context down on SIGINT/SIGTERM, which
    is what ends the loop.
    """
    period = 1.0 / poll_rate_hz if poll_rate_hz > 0 else 1.0
    _logger.info("headless: publishing diagnostics every %.2f s; Ctrl-C to stop", period)
    next_poll = time.monotonic()
    try:
        while rclpy.ok():
            snapshot = data_source.poll()
            if not rclpy.ok():
                break
            on_snapshot(snapshot)
            next_poll += period
            time.sleep(max(0.0, next_poll - time.monotonic()))
    except KeyboardInterrupt:
        pass
    except Exception:
        if rclpy.ok():
            raise
        # Shut down mid-poll (a publish on a dead context): that's the stop signal.
    _logger.info("headless: stopped")


#: `spin_once`'s timeout, and how often `watch`'s spin thread runs `maintain()`.
#: The timeout must stay the same on every call: see `_make_executor`.
_SPIN_TIMEOUT_SECONDS = 0.1


def _make_executor(node: rclpy.node.Node) -> Executor:
    """An executor that owns `node` for the whole session: rclpy's EventsExecutor where there is one.

    `SingleThreadedExecutor` rebuilds a wait set over every subscription and
    its QoS event handlers on each wait. On the first real robot (~4,700
    msg/s, 2026-11-23) that alone took a whole core, and Testudo fell behind
    its own topics; the EventsExecutor (rclpy >= 7.1, Jazzy) took a fifth of
    a core for the same load (`tools/load_bench.py`). Humble has none, and
    `TESTUDO_EXECUTOR=single` asks for the old one, should the experimental
    executor misbehave in the field.

    Not `rclpy.spin_once(node, ...)`: that adds the node to the global
    executor and removes it again on every call, and the executor also
    starts over (a fresh wait set) whenever `spin_once`'s arguments change.
    Either way, every call builds a wait set over all ~100+ subscriptions
    and then runs only the first ready callback in it -- timers first,
    then subscriptions in creation order. Under load, the topics created
    last never got a turn ("publisher(s) present but no messages
    received" on a full-scale sim). With the node kept and a constant
    timeout, one wait's ready callbacks are all run before the next wait.
    """
    executor: Executor | None = None
    if os.environ.get("TESTUDO_EXECUTOR", "").lower() != "single":
        try:
            from rclpy.experimental import EventsExecutor

            executor = EventsExecutor()
        except ImportError:
            pass
    if executor is None:
        executor = SingleThreadedExecutor()
    _logger.info("spinning with %s", type(executor).__name__)
    executor.add_node(node)
    return executor


def _spin_until_stopped(
    executor: Executor, stop_event: threading.Event, manager: SubscriptionManager
) -> None:
    next_maintain = time.monotonic()
    while not stop_event.is_set() and rclpy.ok():
        try:
            executor.spin_once(timeout_sec=_SPIN_TIMEOUT_SECONDS)
            if time.monotonic() >= next_maintain:
                manager.maintain()
                next_maintain = time.monotonic() + _SPIN_TIMEOUT_SECONDS
        except ExternalShutdownException:
            return  # Ctrl-C/SIGTERM (headless): rclpy's signal handler shut the context down
        except RCLError:
            # The same shutdown, landing between the loop's check and rcl's next call.
            if rclpy.ok():
                raise
            return


def cmd_replay(args: argparse.Namespace) -> int:
    if args.watch and not _dashboard_can_start():
        return 2
    config, config_path = _load_config_or_exit(args.config)
    plugins = discover_all_plugins(config.plugins_dir)
    warn_unknown_metrics(config, plugins, config_path)
    _logger.info(
        "loaded config from %s (%d declared topic type(s)); %d plugin(s) discovered",
        config_path,
        len(config.topics),
        len(plugins),
    )

    # Imported lazily so commands that never touch a bag don't require
    # rosbag2_py to be installed.
    from testudo.core.replay_timeline import replay_with_faults

    # Node profiling is deliberately never wired into replay, live or
    # batch: a bag replay has no corresponding live OS process to sample
    # -- profiling "the CPU/memory of a bag" is meaningless, not a gap to
    # fill later. The "Nodes" category (see tui/categorize.py) simply
    # never appears in a replay's Plugin Panel as a result, the same
    # honest absence any other not-applicable category already gets.
    rclpy.init(args=args.rclpy_args)
    node = rclpy.create_node("testudo_replay")
    try:
        if args.watch:
            return _watch_replay(args.bag, config, plugins, node)
        try:
            end, faults = replay_with_faults(args.bag, config, plugins, node)
        except FileNotFoundError as exc:
            _logger.error("%s", exc)
            return 2
    finally:
        node.destroy_node()
        rclpy.shutdown()

    return print_report(end.reports, end.overall, faults)


def _watch_replay(bag: str, config: TestudoConfig, plugins: list, node: rclpy.node.Node) -> int:
    """`replay --watch`: the bag sampled into frames (`replay_timeline`), played back and scrubbed in the TUI."""
    from testudo.core.replay_timeline import replay_timeline
    from testudo.tui.app import TestudoApp
    from testudo.tui.display_prefs import default_display_prefs_path, load_display_prefs
    from testudo.tui.log_summary import suppressed_terminal_logging
    from testudo.tui.replay_source import ReplayDataSource
    from testudo.tui.terminal_title import REPLAY_SUFFIX, TITLE, terminal_title

    try:
        frames = replay_timeline(bag, config, plugins, node)
    except FileNotFoundError as exc:
        _logger.error("%s", exc)
        return 2
    prefs_path = default_display_prefs_path()
    app = TestudoApp(
        display_prefs=load_display_prefs(prefs_path),
        display_prefs_path=prefs_path,
        data_source=ReplayDataSource(frames, config.exclude_topics),
        ros_distro=os.environ.get("ROS_DISTRO", "unknown"),
        ros_domain_id=os.environ.get("ROS_DOMAIN_ID", "0"),
        dds_implementation=_dds_implementation(),
        sim_time_active=True,
    )
    with suppressed_terminal_logging(), terminal_title(TITLE + REPLAY_SUFFIX, window_icon=True):
        app.run()
    return 0


def cmd_plugins(args: argparse.Namespace) -> int:
    config, _config_path = _load_config_or_exit(args.config)
    discovered = discover_all_plugins(config.plugins_dir)
    if not discovered:
        _logger.info("no plugins discovered")
        return 0
    for plugin in discovered:
        msg_types = ", ".join(plugin.plugin_class.msg_types())
        print(f"{plugin.name:20s} [{plugin.source:8s}] handles: {msg_types}")
    return 0


def cmd_config_init(args: argparse.Namespace) -> int:
    """`config init`: declare every topic the plugins cover on the running stack, in a new config file."""
    from testudo.core.config_init import GRAPH_WAIT_SECONDS, read_graph, render_config
    from testudo.core.config_writer import edit_config_file

    path = args.config or DEFAULT_CONFIG_PATH
    if os.path.exists(path) and not args.force:
        _logger.error("%s already exists: pass --force to replace it, or -c to write somewhere else", path)
        return 2

    plugins = discover_all_plugins(None)
    rclpy.init(args=args.rclpy_args)
    node = rclpy.create_node("testudo_config_init")
    try:
        # A new node sees the graph only after discovery has reached it.
        time.sleep(GRAPH_WAIT_SECONDS)
        graph = read_graph(node)
    finally:
        node.destroy_node()
        rclpy.try_shutdown()

    if not graph.topics:
        _logger.error("no publishers found: is the stack running, on this ROS_DOMAIN_ID? Nothing was written")
        return 1
    rendered = render_config(graph, plugins)
    try:
        edit_config_file(path, lambda _text: rendered.text)
    except OSError as exc:
        _logger.error("could not write %s: %s", path, exc)
        return 2
    _logger.info(
        "wrote %s: %d topic(s) and %d action(s) declared, %d camera/point cloud topic(s) left commented out",
        path,
        rendered.topics,
        rendered.actions,
        rendered.heavy_topics,
    )
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    return cmd_config_init(args)


_COMMANDS = {
    "watch": cmd_watch,
    "replay": cmd_replay,
    "plugins": cmd_plugins,
    "config": cmd_config,
}


def split_ros_args(argv: list[str]) -> tuple[list[str], list[str]]:
    """(Testudo's own arguments, the arguments for `rclpy.init`) from a full command line.

    ROS arguments (`--ros-args -p use_sim_time:=true ...`, as `ros2 run`
    or a launch file passes them) are not argparse's: they're removed
    from what it parses and handed to rclpy whole, which picks them out
    itself -- the only way to set `use_sim_time` (or remap the node) for
    `watch`/`replay`.
    """
    return remove_ros_args(args=argv), ["testudo", *argv]


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    cli_argv, rclpy_args = split_ros_args(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(cli_argv)
    args.rclpy_args = rclpy_args
    _configure_logging(args.verbose)
    # `no_hz` (per-subcommand, absent entirely for replay/plugins) and
    # `no_hz_global` (top-level, always present) are merged here rather
    # than sharing a dest -- see `_build_parser`'s comment for why.
    args.no_hz = getattr(args, "no_hz", False) or args.no_hz_global
    return _COMMANDS[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
