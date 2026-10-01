"""NodeProfiler: rediscovery -> tick -> reports/overall_status lifecycle.

Uses a fake rclpy node (just enough surface: get_node_names_and_namespaces,
create_timer, destroy_timer) and monkeypatches psutil.Process so no live
process or ROS graph is needed.
"""
from __future__ import annotations

from collections import namedtuple

import psutil
import pytest

from testudo.core.config import NodeProfilingConfig
from testudo.core.node_profiling.profiler import NodeProfiler
from testudo.plugins.base import Severity, ThresholdZone

_CtxSwitches = namedtuple("ctxsw", ["voluntary", "involuntary"])


class _FakeTimer:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class _FakeNode:
    def __init__(self, node_names: list[tuple[str, str]]) -> None:
        # get_node_names_and_namespaces() returns (name, namespace) tuples.
        self._node_names = node_names

    def get_node_names_and_namespaces(self):
        return self._node_names

    def create_timer(self, period, callback):
        return _FakeTimer()

    def destroy_timer(self, timer):
        timer.cancelled = True


class _FakeProcess:
    """A psutil.Process stand-in with a fixed, injectable resource reading."""

    _next_by_pid: dict[int, dict] = {}

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self._values = _FakeProcess._next_by_pid.get(pid, {"cpu": 10.0, "mem_mb": 50.0, "threads": 2, "children": 0})

    def cpu_percent(self, interval=None):
        return self._values["cpu"]

    def memory_info(self):
        class _Mem:
            pass

        m = _Mem()
        m.rss = int(self._values["mem_mb"] * 1024 * 1024)
        return m

    def num_threads(self):
        return self._values["threads"]

    def children(self, recursive=True):
        return [object()] * self._values["children"]

    def cpu_affinity(self):
        return [0, 1]

    def memory_percent(self):
        return 1.0

    def status(self):
        return self._values.get("status", "running")

    def create_time(self):
        return 0.0

    def num_ctx_switches(self):
        return _CtxSwitches(0, self._values.get("involuntary_ctx_switches", 0))

    def nice(self):
        return 0

    def username(self):
        return "robot"

    def num_fds(self):
        return 5


@pytest.fixture(autouse=True)
def _patch_psutil_process(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(psutil, "Process", _FakeProcess)
    # Rediscovery's default process_iter is real psutil.process_iter unless
    # a test overrides it (several below do, with their own fake) -- an
    # empty default here keeps tests that don't care about resolution
    # (only about tick()'s sampling behavior) from scanning every real
    # process on the machine through the now-patched (and incompatible)
    # _FakeProcess above.
    monkeypatch.setattr(psutil, "process_iter", lambda *_args, **_kwargs: iter([]))
    _FakeProcess._next_by_pid = {}
    yield
    _FakeProcess._next_by_pid = {}


def _config(**overrides) -> NodeProfilingConfig:
    defaults = dict(
        cpu_percent=ThresholdZone(green=50.0, orange=90.0),
        memory_mb=ThresholdZone(green=100.0, orange=500.0),
        gpu_enabled=False,
    )
    defaults.update(overrides)
    return NodeProfilingConfig(**defaults)


def test_start_resolves_self_even_with_no_ros_nodes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("os.getpid", lambda: 555)
    node = _FakeNode(node_names=[])
    profiler = NodeProfiler(node, _config())
    profiler.start()
    profiler.tick()
    reports = profiler.reports()
    assert len(reports) == 1
    assert reports[0].pid == 555
    assert reports[0].resolved is False


def test_tick_twice_produces_real_reading_after_warmup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("os.getpid", lambda: 555)
    node = _FakeNode(node_names=[])
    profiler = NodeProfiler(node, _config())
    profiler.start()

    profiler.tick()
    first = profiler.reports()[0]
    assert first.status.values.get("cpu_percent") == "warming up"

    profiler.tick()
    second = profiler.reports()[0]
    assert second.cpu_percent == 10.0
    assert second.memory_rss_mb == 50.0
    assert second.status.severity == Severity.OK


def test_high_cpu_crosses_into_error_after_hysteresis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("os.getpid", lambda: 555)
    _FakeProcess._next_by_pid = {555: {"cpu": 95.0, "mem_mb": 10.0, "threads": 1, "children": 0}}
    node = _FakeNode(node_names=[])
    profiler = NodeProfiler(node, _config(), hysteresis_required_consecutive=2)
    profiler.start()

    profiler.tick()  # warming up -- always OK
    assert profiler.reports()[0].status.severity == Severity.OK
    profiler.tick()  # 1st real ERROR-worthy sample -- not enough consecutive samples yet
    assert profiler.reports()[0].status.severity == Severity.OK
    profiler.tick()  # 2nd consecutive -- now debounced severity flips
    assert profiler.reports()[0].status.severity == Severity.ERROR


def test_rediscovery_resolves_ros_node_and_updates_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("os.getpid", lambda: 555)
    node = _FakeNode(node_names=[("controller_server", "/")])

    # process_match resolution is exercised via the real resolve_processes,
    # patched here with a fake process_iter so no live process is needed.
    import testudo.core.node_profiling.process_match as process_match_module

    class _FakeProc:
        def __init__(self, pid, cmdline, name):
            self.pid = pid
            self.info = {"pid": pid, "cmdline": cmdline, "name": name}

    def fake_process_iter(_attrs):
        return iter(
            [_FakeProc(777, ["controller_server", "-r", "__node:=controller_server"], "controller_server")]
        )

    monkeypatch.setattr(process_match_module.psutil, "process_iter", fake_process_iter)

    profiler = NodeProfiler(node, _config())
    profiler.start()
    profiler.tick()
    profiler.tick()

    identities = {stat.identity for stat in profiler.reports()}
    assert "/controller_server" in identities
    assert any(stat.pid == 555 for stat in profiler.reports())  # self always present


def test_dead_pid_dropped_after_rediscovery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("os.getpid", lambda: 555)
    node = _FakeNode(node_names=[("controller_server", "/")])

    import testudo.core.node_profiling.process_match as process_match_module

    class _FakeProc:
        def __init__(self, pid, cmdline, name):
            self.pid = pid
            self.info = {"pid": pid, "cmdline": cmdline, "name": name}

    present = [_FakeProc(777, ["controller_server", "-r", "__node:=controller_server"], "controller_server")]

    def fake_process_iter(_attrs):
        return iter(present)

    monkeypatch.setattr(process_match_module.psutil, "process_iter", fake_process_iter)
    profiler = NodeProfiler(node, _config())
    profiler.start()
    profiler.tick()
    assert any(stat.pid == 777 for stat in profiler.reports())

    present.clear()  # the controller_server process exited
    profiler._on_rediscovery_timer()
    profiler.tick()
    assert all(stat.pid != 777 for stat in profiler.reports())


def test_exclude_nodes_filters_matched_process(monkeypatch: pytest.MonkeyPatch) -> None:
    from testudo.core.config import ExcludeRule

    monkeypatch.setattr("os.getpid", lambda: 555)
    node = _FakeNode(node_names=[("controller_server", "/")])

    import testudo.core.node_profiling.process_match as process_match_module

    class _FakeProc:
        def __init__(self, pid, cmdline, name):
            self.pid = pid
            self.info = {"pid": pid, "cmdline": cmdline, "name": name}

    def fake_process_iter(_attrs):
        return iter([_FakeProc(777, ["controller_server", "-r", "__node:=controller_server"], "controller_server")])

    monkeypatch.setattr(process_match_module.psutil, "process_iter", fake_process_iter)
    profiler = NodeProfiler(node, _config(exclude_nodes=[ExcludeRule("/controller_server")]))
    profiler.start()
    profiler.tick()
    identities = {stat.identity for stat in profiler.reports()}
    assert "/controller_server" not in identities
    assert any(stat.pid == 555 for stat in profiler.reports())  # self is never excludable


def test_close_cancels_timer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("os.getpid", lambda: 555)
    node = _FakeNode(node_names=[])
    profiler = NodeProfiler(node, _config())
    profiler.start()
    timer = profiler._timer
    profiler.close()
    assert timer.cancelled is True
    assert profiler._timer is None


def test_reset_stats_clears_debounce_history(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("os.getpid", lambda: 555)
    _FakeProcess._next_by_pid = {555: {"cpu": 95.0, "mem_mb": 10.0, "threads": 1, "children": 0}}
    node = _FakeNode(node_names=[])
    profiler = NodeProfiler(node, _config(), hysteresis_required_consecutive=2)
    profiler.start()
    profiler.tick()
    profiler.tick()
    profiler.tick()
    assert profiler.reports()[0].status.severity == Severity.ERROR

    profiler.reset_stats()
    profiler.tick()  # fresh sampler -> warming up again
    assert profiler.reports()[0].status.values.get("cpu_percent") == "warming up"


def test_starving_node_flagged_via_involuntary_ctx_switches(monkeypatch: pytest.MonkeyPatch) -> None:
    import testudo.core.node_profiling.sampler as sampler_module

    monkeypatch.setattr("os.getpid", lambda: 555)
    _FakeProcess._next_by_pid = {
        555: {"cpu": 5.0, "mem_mb": 10.0, "threads": 4, "children": 0, "involuntary_ctx_switches": 0}
    }
    fake_time = {"now": 1000.0}
    monkeypatch.setattr(sampler_module.time, "monotonic", lambda: fake_time["now"])
    node = _FakeNode(node_names=[])
    profiler = NodeProfiler(
        node, _config(ctx_switches_per_sec=ThresholdZone(green=10.0, orange=50.0)), hysteresis_required_consecutive=1
    )
    profiler.start()
    profiler.tick()  # warming up -- seeds both cpu and ctx-switch baselines

    fake_time["now"] += 1.0
    _FakeProcess._next_by_pid[555]["involuntary_ctx_switches"] = 1000  # 1000/s over the elapsed 1s -- way past 50/s orange
    profiler.tick()
    status = profiler.reports()[0].status
    assert status.severity == Severity.ERROR
    assert "NODE-001" in status.codes


def test_disk_sleep_node_flagged_even_with_healthy_cpu_and_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("os.getpid", lambda: 555)
    _FakeProcess._next_by_pid = {555: {"cpu": 1.0, "mem_mb": 5.0, "threads": 1, "children": 0, "status": "disk-sleep"}}
    node = _FakeNode(node_names=[])
    profiler = NodeProfiler(node, _config(), hysteresis_required_consecutive=1)
    profiler.start()
    profiler.tick()

    status = profiler.reports()[0].status
    assert status.severity == Severity.WARN
    assert "NODE-002" in status.codes
