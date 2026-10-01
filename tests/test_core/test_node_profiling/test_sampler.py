"""ProcessSampler: cached-Process warm-up behavior and platform guards -- no live process required."""
from __future__ import annotations

from collections import namedtuple

import psutil
import pytest

from testudo.core.node_profiling.sampler import ProcessSampler

_CtxSwitches = namedtuple("ctxsw", ["voluntary", "involuntary"])


class _FakeProcess:
    """A configurable psutil.Process stand-in covering every accessor ProcessSampler calls."""

    def __init__(
        self,
        pid: int,
        *,
        cpu_percent: float = 42.0,
        mem_rss_bytes: int = 100 * 1024 * 1024,
        mem_percent: float = 1.5,
        threads: int = 4,
        children: int = 0,
        affinity: list[int] | None = None,
        status: str = "running",
        create_time: float = 0.0,
        voluntary: int = 0,
        involuntary: int = 0,
        nice: int | None = 0,
        username: str | None = "robot",
        num_fds: int | None = 12,
        raise_on: str | None = None,
    ) -> None:
        self.pid = pid
        self._cpu_percent = cpu_percent
        self._mem_rss_bytes = mem_rss_bytes
        self._mem_percent = mem_percent
        self._threads = threads
        self._children = children
        self._affinity = [0, 1, 2, 3] if affinity is None else affinity
        self._status = status
        self._create_time = create_time
        self._voluntary = voluntary
        self._involuntary = involuntary
        self._nice = nice
        self._username = username
        self._num_fds = num_fds
        self._raise_on = raise_on

    def cpu_percent(self, interval=None):
        return self._cpu_percent

    def memory_info(self):
        class _Mem:
            pass

        m = _Mem()
        m.rss = self._mem_rss_bytes
        return m

    def memory_percent(self):
        return self._mem_percent

    def num_threads(self):
        return self._threads

    def children(self, recursive=True):
        return [object()] * self._children

    def cpu_affinity(self):
        if self._raise_on == "cpu_affinity":
            raise AttributeError("not supported")
        return self._affinity

    def status(self):
        return self._status

    def create_time(self):
        return self._create_time

    def num_ctx_switches(self):
        return _CtxSwitches(self._voluntary, self._involuntary)

    def nice(self):
        if self._nice is None:
            raise psutil.AccessDenied(pid=self.pid)
        return self._nice

    def username(self):
        if self._username is None:
            raise psutil.AccessDenied(pid=self.pid)
        return self._username

    def num_fds(self):
        if self._num_fds is None:
            raise AttributeError("not supported on this platform")
        return self._num_fds


def test_first_sample_is_warming_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(psutil, "Process", lambda pid: _FakeProcess(pid, cpu_percent=99.0))
    sampler = ProcessSampler()

    first = sampler.sample(123)
    assert first is not None
    assert first.warming_up is True
    assert first.cpu_percent == 0.0
    assert first.ctx_switches_involuntary_per_sec is None
    # Everything not CPU%/ctx-switch-rate-shaped is accurate immediately.
    assert first.status == "running"
    assert first.memory_percent == 1.5

    second = sampler.sample(123)
    assert second is not None
    assert second.warming_up is False
    assert second.cpu_percent == 99.0


def test_process_cached_across_samples(monkeypatch: pytest.MonkeyPatch) -> None:
    constructed = []

    def factory(pid):
        constructed.append(pid)
        return _FakeProcess(pid)

    monkeypatch.setattr(psutil, "Process", factory)
    sampler = ProcessSampler()
    sampler.sample(1)
    sampler.sample(1)
    sampler.sample(1)
    assert constructed == [1]  # one Process() construction, not three


def test_cpu_affinity_unsupported_platform_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(psutil, "Process", lambda pid: _FakeProcess(pid, raise_on="cpu_affinity"))
    sampler = ProcessSampler()
    sampler.sample(1)
    result = sampler.sample(1)
    assert result.cpu_affinity is None


def test_sample_returns_none_for_nonexistent_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    def factory(pid):
        raise psutil.NoSuchProcess(pid=pid)

    monkeypatch.setattr(psutil, "Process", factory)
    sampler = ProcessSampler()
    assert sampler.sample(99999) is None


def test_prune_drops_dead_pids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(psutil, "Process", lambda pid: _FakeProcess(pid))
    sampler = ProcessSampler()
    sampler.sample(1)
    sampler.sample(2)
    assert set(sampler._handles) == {1, 2}
    sampler.prune({1})
    assert set(sampler._handles) == {1}
    assert set(sampler._ctx_switch_baseline) == {1}


def test_nice_username_num_fds_gracefully_degrade_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        psutil, "Process", lambda pid: _FakeProcess(pid, nice=None, username=None, num_fds=None)
    )
    sampler = ProcessSampler()
    sampler.sample(1)
    result = sampler.sample(1)
    assert result.nice is None
    assert result.username is None
    assert result.num_fds is None


def test_involuntary_ctx_switch_rate_computed_from_second_sample_onward(monkeypatch: pytest.MonkeyPatch) -> None:
    import testudo.core.node_profiling.sampler as sampler_module

    counts = {"involuntary": 100}
    fake_time = {"now": 1000.0}

    class _Growing(_FakeProcess):
        def num_ctx_switches(self):
            return _CtxSwitches(0, counts["involuntary"])

    monkeypatch.setattr(psutil, "Process", lambda pid: _Growing(pid))
    monkeypatch.setattr(sampler_module.time, "monotonic", lambda: fake_time["now"])
    sampler = ProcessSampler()

    first = sampler.sample(1)
    assert first.ctx_switches_involuntary_per_sec is None  # baseline just seeded, nothing to diff against yet

    fake_time["now"] += 2.0
    counts["involuntary"] = 500  # +400 over 2s -> 200/s
    second = sampler.sample(1)
    assert second.ctx_switches_involuntary_per_sec == pytest.approx(200.0)
