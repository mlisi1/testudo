"""Orchestrates node/process resource profiling: rediscovery + periodic sampling.

Follows the same two-seam thread-ownership split `SubscriptionManager`
uses (see `maintenance.py`'s module docstring): `maintain()`/`rediscover()`
touches the ROS graph and must run on whichever thread spins `node`;
`tick()` is pure psutil work -- no rclpy calls -- so it's safe to call
from any thread, exactly like `SubscriptionManager.tick()` already is. No
bespoke thread or lock: each seam owns and swaps one piece of state
atomically (`_mapping`, `_latest`), relying on the same "plain reference
swap is safe under the GIL" pattern `subscription_manager.py` documents
for itself.
"""
from __future__ import annotations

import os
from collections import Counter
from typing import TYPE_CHECKING

from testudo.core.aggregator import DEFAULT_REQUIRED_CONSECUTIVE, HysteresisDebouncer
from testudo.core.config import NodeProfilingConfig, SeverityMode
from testudo.core.node_profiling import maintenance as _maintenance
from testudo.core.node_profiling.gpu import per_process_gpu_memory_mb
from testudo.core.node_profiling.process_match import ResolvedProcess
from testudo.core.node_profiling.report import NodeStat, build_status
from testudo.core.node_profiling.sampler import ProcessSampler
from testudo.plugins.base import SEVERITY_LABELS, CheckStatus, Severity

if TYPE_CHECKING:
    import rclpy.node


class NodeProfiler:
    """Tracks every resolvable ROS node (plus Testudo itself) and its OS-process resource usage."""

    def __init__(
        self,
        node: "rclpy.node.Node",
        config: NodeProfilingConfig,
        hysteresis_required_consecutive: int = DEFAULT_REQUIRED_CONSECUTIVE,
    ) -> None:
        self._node = node
        self._config = config
        self._self_pid = os.getpid()
        self._sampler = ProcessSampler()
        self._mapping: dict[int, ResolvedProcess] = {}
        self._latest: tuple[NodeStat, ...] = ()
        self._debouncers: dict[str, HysteresisDebouncer] = {}
        self._hysteresis_required_consecutive = hysteresis_required_consecutive
        self._timer = None

    def start(self) -> None:
        """Run an initial rediscovery pass and arm the periodic one.

        The rediscovery timer is created on `self._node`, so it always
        fires on whichever thread is spinning that node (the dedicated
        spin thread in `cmd_watch`, the single main thread in
        `cmd_check`) -- the same thread-ownership guarantee
        `SubscriptionManager.maintain()` needs, achieved here without any
        cli.py loop-plumbing changes.
        """
        _maintenance.rediscover(self)
        self._timer = self._node.create_timer(self._config.rediscovery_interval_seconds, self._on_rediscovery_timer)

    def _on_rediscovery_timer(self) -> None:
        _maintenance.rediscover(self)

    def tick(self) -> None:
        """Sample every currently-mapped process. Callable from any thread (no rclpy calls)."""
        mapping = self._mapping  # one reference read -- stable for the rest of this call
        latest: list[NodeStat] = []
        for resolved in mapping.values():
            sample = self._sampler.sample(resolved.pid)
            if sample is None:
                continue
            gpu_mb = per_process_gpu_memory_mb(resolved.pid) if self._config.gpu_enabled else None
            status = build_status(
                sample,
                resolved.pid,
                resolved.process_name,
                self._config.cpu_percent,
                self._config.memory_mb,
                self._config.ctx_switches_per_sec,
                gpu_mb,
            )
            debouncer = self._debouncers.setdefault(
                resolved.identity, HysteresisDebouncer(self._hysteresis_required_consecutive)
            )
            status = debouncer.update(status)
            latest.append(
                NodeStat(
                    identity=resolved.identity,
                    pid=resolved.pid,
                    process_name=resolved.process_name,
                    resolved=resolved.resolved,
                    cpu_percent=sample.cpu_percent,
                    cpu_affinity=sample.cpu_affinity,
                    num_threads=sample.num_threads,
                    num_children=sample.num_children,
                    memory_rss_mb=sample.memory_rss_mb,
                    gpu_memory_mb=gpu_mb,
                    status=status,
                )
            )
        live_identities = {resolved.identity for resolved in mapping.values()}
        self._debouncers = {identity: d for identity, d in self._debouncers.items() if identity in live_identities}
        # One atomic swap for reports() to read, same pattern _mapping uses above.
        self._latest = tuple(sorted(latest, key=lambda stat: stat.identity))

    def reports(self) -> tuple[NodeStat, ...]:
        """Current resource reading for every tracked process, as of the last `tick()`."""
        return self._latest

    def overall_status(self, mode: SeverityMode) -> CheckStatus:
        """The aggregated (worst/weighted/both) status across every currently tracked process."""
        return _aggregate(self._latest, mode)

    def reset_stats(self) -> None:
        """Clear accumulated rolling-window state (cached process handles, debounce history).

        Backs the same 'r' keybind as `SubscriptionManager.reset_stats()`;
        deliberately doesn't touch `_mapping` -- rediscovery still runs on
        its own schedule, not tied to a stats reset.
        """
        self._sampler = ProcessSampler()
        self._debouncers.clear()

    def close(self) -> None:
        """Stop the rediscovery timer. Call before destroying the owning node."""
        if self._timer is not None:
            self._timer.cancel()
            self._node.destroy_timer(self._timer)
            self._timer = None


def _aggregate(stats: tuple[NodeStat, ...], mode: SeverityMode) -> CheckStatus:
    """Roll up every tracked process's status -- worst/weighted/both, mirroring `aggregate_reports`.

    A thin, separately-typed sibling of `core/aggregator.aggregate_reports`
    (keyed on `TopicReport`) rather than a reuse of it: `NodeStat` isn't a
    `TopicReport`, and every tracked process is weighted equally (there's
    no per-node `weight:` config knob, unlike topics/actions), so the
    weighted branch collapses to a plain mean.
    """
    if not stats:
        return CheckStatus(severity=Severity.OK, label="overall", message="no processes tracked")

    worst_severity = max(stat.status.severity for stat in stats)
    mean_severity = sum(stat.status.severity for stat in stats) / len(stats)

    counts = Counter(stat.status.severity for stat in stats)
    count_summary = ", ".join(f"{SEVERITY_LABELS[severity]}={counts[severity]}" for severity in sorted(counts))
    message = f"{len(stats)} process(es): {count_summary}"

    if mode == SeverityMode.WEIGHTED:
        severity = max(Severity.OK, min(Severity.STALE, round(mean_severity)))
    else:
        severity = worst_severity

    values = {"processes_tracked": str(len(stats))}
    if mode != SeverityMode.WORST:
        values["weighted_score"] = f"{mean_severity:.3g}"

    return CheckStatus(severity=severity, label="overall", message=message, values=values)
