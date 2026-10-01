"""Tier 1/Tier 2 resolution against a synthetic process list -- no live processes needed."""
from __future__ import annotations

from testudo.core.node_profiling.process_match import ResolvedProcess, resolve_processes


class _FakeProc:
    def __init__(self, pid: int, cmdline: list[str], name: str) -> None:
        self.pid = pid
        self.info = {"pid": pid, "cmdline": cmdline, "name": name}


def _fake_process_iter(procs: list[_FakeProc]):
    def process_iter(_attrs):
        return iter(procs)

    return process_iter


def test_exact_remap_match_wins() -> None:
    procs = [
        _FakeProc(101, ["controller_server", "--ros-args", "-r", "__node:=controller_server"], "controller_server"),
    ]
    resolved = resolve_processes(["/controller_server"], self_pid=999, process_iter=_fake_process_iter(procs))
    by_pid = {r.pid: r for r in resolved}
    assert by_pid[101] == ResolvedProcess(pid=101, identity="/controller_server", process_name="controller_server", resolved=True)


def test_fallback_basename_match() -> None:
    # No __node:= remap -- falls back to exe basename == candidate's bare name.
    procs = [_FakeProc(202, ["amcl"], "amcl")]
    resolved = resolve_processes(["/amcl"], self_pid=999, process_iter=_fake_process_iter(procs))
    by_pid = {r.pid: r for r in resolved}
    assert by_pid[202].identity == "/amcl"
    assert by_pid[202].resolved is True


def test_fallback_matches_script_argument_not_just_interpreter() -> None:
    """Regression: a Python-run node's cmdline[0] is the interpreter ("python3"), not
    anything node-shaped -- the fallback must also check later positional tokens
    (the script path), not just cmdline[0], or every non-compiled node silently
    never resolves."""
    procs = [_FakeProc(303, ["python3", "/opt/ros/jazzy/lib/my_pkg/controller_server"], "python3")]
    resolved = resolve_processes(["/controller_server"], self_pid=999, process_iter=_fake_process_iter(procs))
    by_pid = {r.pid: r for r in resolved}
    assert by_pid[303].identity == "/controller_server"
    assert by_pid[303].resolved is True


def test_fallback_matches_script_run_directly_with_py_extension() -> None:
    """Regression: `python3 controller_server.py` keeps the `.py` suffix in argv --
    matching must strip a trailing file extension before comparing basenames."""
    procs = [_FakeProc(404, ["python3", "/home/dev/controller_server.py"], "python3")]
    resolved = resolve_processes(["/controller_server"], self_pid=999, process_iter=_fake_process_iter(procs))
    by_pid = {r.pid: r for r in resolved}
    assert by_pid[404].identity == "/controller_server"
    assert by_pid[404].resolved is True


def test_fallback_does_not_match_flags_only_positional_tokens() -> None:
    procs = [_FakeProc(505, ["amcl", "--ros-args", "-p", "use_sim_time:=true"], "amcl")]
    resolved = resolve_processes(["/amcl"], self_pid=999, process_iter=_fake_process_iter(procs))
    by_pid = {r.pid: r for r in resolved}
    assert by_pid[505].identity == "/amcl"


def test_ros2_bag_play_resolves_to_rosbag2_player() -> None:
    """Regression: `ros2 bag play` runs its Player node inside the `ros2` CLI's own
    process -- the node name ("rosbag2_player", hardcoded by rosbag2_transport) never
    appears in argv at all, so no basename heuristic alone can ever find it."""
    procs = [_FakeProc(606, ["/usr/bin/python3", "/opt/ros/jazzy/bin/ros2", "bag", "play", "my_bag/", "-l"], "ros2")]
    resolved = resolve_processes(["/rosbag2_player"], self_pid=999, process_iter=_fake_process_iter(procs))
    by_pid = {r.pid: r for r in resolved}
    assert by_pid[606].identity == "/rosbag2_player"
    assert by_pid[606].resolved is True


def test_ros2_bag_record_resolves_to_rosbag2_recorder() -> None:
    procs = [_FakeProc(707, ["/usr/bin/python3", "/opt/ros/jazzy/bin/ros2", "bag", "record", "-a"], "ros2")]
    resolved = resolve_processes(["/rosbag2_recorder"], self_pid=999, process_iter=_fake_process_iter(procs))
    by_pid = {r.pid: r for r in resolved}
    assert by_pid[707].identity == "/rosbag2_recorder"


def test_ros2_bag_node_name_not_matched_when_candidate_absent() -> None:
    """No /rosbag2_player among candidate node_names -- must not force a false match."""
    procs = [_FakeProc(808, ["/usr/bin/python3", "/opt/ros/jazzy/bin/ros2", "bag", "play", "my_bag/"], "ros2")]
    resolved = resolve_processes([], self_pid=999, process_iter=_fake_process_iter(procs))
    assert all(r.pid != 808 for r in resolved)


def test_bag_before_ros2_does_not_false_match() -> None:
    """"bag" appearing before "ros2" in argv (an unrelated coincidence) must not trigger the special case."""
    procs = [_FakeProc(909, ["mytool", "bag", "something", "ros2"], "mytool")]
    resolved = resolve_processes(["/rosbag2_player"], self_pid=999, process_iter=_fake_process_iter(procs))
    assert all(r.pid != 909 for r in resolved)


def test_namespaced_nodes_not_confused_by_bare_name_match() -> None:
    """Exact remap match distinguishes same-named nodes in different namespaces; fallback can't."""
    procs = [
        _FakeProc(1, ["controller_server", "-r", "__node:=controller_server", "-r", "__ns:=/robot1"], "controller_server"),
        _FakeProc(2, ["controller_server", "-r", "__node:=controller_server", "-r", "__ns:=/robot2"], "controller_server"),
    ]
    resolved = resolve_processes(
        ["/robot1/controller_server", "/robot2/controller_server"], self_pid=999, process_iter=_fake_process_iter(procs)
    )
    by_pid = {r.pid: r for r in resolved}
    assert by_pid[1].identity == "/robot1/controller_server"
    assert by_pid[2].identity == "/robot2/controller_server"


def test_unmatched_process_is_not_tracked() -> None:
    procs = [_FakeProc(303, ["some_unrelated_process"], "some_unrelated_process")]
    resolved = resolve_processes(["/controller_server"], self_pid=999, process_iter=_fake_process_iter(procs))
    assert all(r.pid != 303 for r in resolved)


def test_self_pid_always_included_even_when_unresolved() -> None:
    procs = [_FakeProc(999, ["python3", "testudo", "watch"], "python3")]
    resolved = resolve_processes([], self_pid=999, process_iter=_fake_process_iter(procs))
    by_pid = {r.pid: r for r in resolved}
    assert 999 in by_pid
    assert by_pid[999].resolved is False
    assert by_pid[999].identity == "pid:999"
    assert by_pid[999].process_name == "python3"


def test_self_pid_included_even_if_not_in_live_process_list() -> None:
    """Defensive: self should never simply vanish even if psutil somehow can't see it."""
    resolved = resolve_processes([], self_pid=12345, process_iter=_fake_process_iter([]))
    assert [r.pid for r in resolved] == [12345]
    assert resolved[0].resolved is False


def test_process_that_disappears_during_info_access_is_skipped() -> None:
    import psutil

    class _DyingProc:
        @property
        def info(self):
            raise psutil.NoSuchProcess(pid=404)

    def process_iter(_attrs):
        return iter([_DyingProc()])

    resolved = resolve_processes([], self_pid=999, process_iter=process_iter)
    assert [r.pid for r in resolved] == [999]
