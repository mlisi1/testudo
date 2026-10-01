"""psutil-based per-process resource sampling.

`psutil.Process.cpu_percent()` compares CPU time against a baseline stored
on the `Process` instance from its own previous call -- a freshly
constructed `Process` per tick has no baseline and returns 0.0 forever.
`ProcessSampler` caches one `Process` per tracked PID across ticks and
seeds that baseline on first sighting, flagging that tick's reading as
"warming up" rather than reporting a bogus 0%. The involuntary-context-
switch *rate* (see `ProcessSample.ctx_switches_involuntary_per_sec`) needs
the same two-sample treatment -- `num_ctx_switches()` is a cumulative
counter, not a rate, and psutil has no built-in delta for it the way it
does for `cpu_percent()` -- so `ProcessSampler` tracks its own baseline
for that alongside the cached `Process` handle.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import psutil


@dataclass(frozen=True)
class ProcessSample:
    """One tick's resource reading for one tracked PID."""

    cpu_percent: float
    cpu_affinity: tuple[int, ...] | None
    num_threads: int
    num_children: int
    memory_rss_mb: float
    memory_percent: float
    #: psutil's process state string: "running", "sleeping", "disk-sleep"
    #: (blocked in uninterruptible I/O -- the classic "starving on I/O"
    #: signal), "zombie" (dead, not yet reaped by its parent), "stopped", etc.
    status: str
    #: Wall-clock seconds since the process started (`time.time() -
    #: create_time()`) -- process lifetime isn't meaningful in sim time.
    uptime_seconds: float
    #: Cumulative counts since the process started; `_involuntary` is also
    #: available as a per-second rate below, the actual CPU-starvation signal
    #: (a process the OS scheduler keeps preempting against its will, as
    #: opposed to a voluntary switch from blocking on I/O/a lock, which is
    #: normal and not itself a sign of trouble).
    ctx_switches_voluntary: int
    ctx_switches_involuntary: int
    #: None on the first sample for a PID (no prior count to diff against
    #: -- same "warming up" shape as cpu_percent, computed the moment a
    #: real baseline exists rather than gated on the same tick as cpu_percent's).
    ctx_switches_involuntary_per_sec: float | None
    nice: int | None
    username: str | None
    #: Open file descriptor count -- Linux-only; None elsewhere.
    num_fds: int | None
    warming_up: bool


class ProcessSampler:
    """Caches `psutil.Process` handles across ticks and samples resource usage."""

    def __init__(self) -> None:
        self._handles: dict[int, psutil.Process] = {}
        #: pid -> (monotonic timestamp, involuntary count) as of the last
        #: sample, for the involuntary-context-switch rate calculation.
        self._ctx_switch_baseline: dict[int, tuple[float, int]] = {}

    def sample(self, pid: int) -> ProcessSample | None:
        """Sample `pid`'s current resource usage, or None if it no longer exists.

        The first sample for a newly-tracked `pid` only seeds
        `cpu_percent()`'s baseline (`warming_up=True`, `cpu_percent=0.0`,
        `ctx_switches_involuntary_per_sec=None`) -- real readings are
        available from the *next* call onward, once an interval has
        actually elapsed for the comparison.
        """
        handle = self._handles.get(pid)
        warming_up = handle is None
        if handle is None:
            try:
                handle = psutil.Process(pid)
                handle.cpu_percent(interval=None)  # seed the baseline
            except psutil.NoSuchProcess:
                return None
            self._handles[pid] = handle

        try:
            cpu_percent = 0.0 if warming_up else handle.cpu_percent(interval=None)
            memory_info = handle.memory_info()
            memory_rss_mb = memory_info.rss / (1024 * 1024)
            memory_percent = handle.memory_percent()
            num_threads = handle.num_threads()
            num_children = len(handle.children(recursive=True))
            cpu_affinity = self._cpu_affinity(handle)
            status = handle.status()
            uptime_seconds = max(0.0, time.time() - handle.create_time())
            voluntary, involuntary, rate = self._ctx_switch_rate(pid, handle)
            nice = self._optional(handle.nice)
            username = self._optional(handle.username)
            num_fds = self._optional(handle.num_fds)
        except psutil.NoSuchProcess:
            self.drop(pid)
            return None

        return ProcessSample(
            cpu_percent=cpu_percent,
            cpu_affinity=cpu_affinity,
            num_threads=num_threads,
            num_children=num_children,
            memory_rss_mb=memory_rss_mb,
            memory_percent=memory_percent,
            status=status,
            uptime_seconds=uptime_seconds,
            ctx_switches_voluntary=voluntary,
            ctx_switches_involuntary=involuntary,
            ctx_switches_involuntary_per_sec=rate,
            nice=nice,
            username=username,
            num_fds=num_fds,
            warming_up=warming_up,
        )

    def _ctx_switch_rate(self, pid: int, handle: psutil.Process) -> tuple[int, int, float | None]:
        """(voluntary, involuntary, involuntary-per-second-since-last-sample-or-None)."""
        counts = handle.num_ctx_switches()
        now = time.monotonic()
        baseline = self._ctx_switch_baseline.get(pid)
        self._ctx_switch_baseline[pid] = (now, counts.involuntary)
        if baseline is None:
            return counts.voluntary, counts.involuntary, None
        baseline_time, baseline_involuntary = baseline
        elapsed = now - baseline_time
        if elapsed <= 0:
            return counts.voluntary, counts.involuntary, None
        rate = max(0.0, (counts.involuntary - baseline_involuntary) / elapsed)
        return counts.voluntary, counts.involuntary, rate

    @staticmethod
    def _cpu_affinity(handle: psutil.Process) -> tuple[int, ...] | None:
        """`None` on a platform without CPU-affinity support (e.g. macOS), not a crash."""
        try:
            return tuple(handle.cpu_affinity())
        except (AttributeError, NotImplementedError):
            return None

    @staticmethod
    def _optional(getter) -> object | None:
        """Call a zero-arg `psutil.Process` accessor, `None` on any platform/permission gap.

        `nice()`/`username()`/`num_fds()` each have their own way of being
        unavailable (missing on a platform, `AccessDenied` on a process
        Testudo doesn't own) -- none of them should turn "more detail" into
        a crashed tick.
        """
        try:
            return getter()
        except (AttributeError, NotImplementedError, psutil.AccessDenied, psutil.NoSuchProcess):
            return None

    def drop(self, pid: int) -> None:
        """Stop tracking `pid` (it no longer exists) -- next sighting starts a fresh baseline."""
        self._handles.pop(pid, None)
        self._ctx_switch_baseline.pop(pid, None)

    def prune(self, live_pids: set[int]) -> None:
        """Drop cached handles for any PID not in `live_pids` (dead-PID sweep)."""
        for pid in list(self._handles):
            if pid not in live_pids:
                self.drop(pid)
